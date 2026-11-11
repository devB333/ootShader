#!/usr/bin/env python3
"""Find out which pipelines of a hints file the importer cannot rebuild, where their shaders are, and why.

`hints.py import` (and scb_oot.py) only say "skipped N pipelines". This tool explains every one of them and
writes a log. It never changes the hints, the game file or an emulator cache.

  python3 tools/diagnose_missing.py                                  # hints only: what can be told without game files
  python3 tools/diagnose_missing.py --nup GAME.nup                   # + scan the .nup for shader containers the extractor skips
  python3 tools/diagnose_missing.py --nup GAME.nup --work WORK       # full: WORK is a build_code_store.py folder; it is built
                                                                     #   when it does not exist (needs --lichii and g++ or --bin-dir)

What it checks, per skipped pipeline and per shader slot:
  * the stage the emulator recorded (vertex, fragment, task, mesh, compute) and the code size it expects;
  * for task/mesh stages, the MeshStateHash the emulator XORs into the key (computed from the recorded
    mesh registers and primitive header), which gives the real hash of the shader code;
  * whether that code is in the code store (gfx, mesh or compute), under which hash and with which size;
  * if it is not: which extracted program it most likely is (the partner of the shaders that were found in the
    same archive, or a program with the same native header), and whether that program failed to lower (with
    Lichii's error text), lowered to different code, or was never handed to the lowering step;
  * which containers the extractor could not read, and which files in the .nup hold shader containers but
    are not on the list build_code_store.py reads.

Output: a summary on the console, <out>/missing_shaders.log (full report) and <out>/missing_shaders.tsv (one
row per skipped pipeline). They hold hashes, sizes and file names, no shader code.
"""
import argparse, collections, datetime, json, os, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convert_cache as cc
import hints as H
import build_code_store as B
from cityhash import city_hash64

ROOT = os.path.dirname(HERE)
EXE = B.EXE
SLOTS = ('VertexA', 'VertexB', 'TessControl', 'TessEval', 'Geometry', 'Fragment')
# stage numbers of the cache (docs/NOTES.md) -> stage names of the extractor's manifest
STAGES = {0: 'vertex', 1: 'tess-control', 2: 'tess-eval', 3: 'geometry', 4: 'fragment', 5: 'compute',
          6: 'vertex', 7: 'task', 8: 'mesh'}
TASK, MESH, COMPUTE = 7, 8, 5

# causes, in the order they are reported
MESH_ONLY = 'mesh shader without a task shader (importer never looks in the mesh store)'
MESH_STATE = 'task+mesh pipeline whose mesh state the importer could not resolve'
SIZE = 'shader found, but its code size differs from the recorded one'
COUNT = 'number of recorded environments does not match the number of shader hashes'
NOT_REPRODUCED = 'shader code is not in the code store'
COMPUTE_MISSING = 'compute shader code is not in the code store'
COMPUTE_SIZE = 'compute shader found, but its code size differs from the recorded one'
UNKNOWN = 'cannot tell without the code store (run again with --work)'


# ---- the recorded environment (hints "meta" blob) ----------------------------------------------
class Env:
    """The 76 byte environment header plus what follows the shader code (see hints.split_record)."""

    def __init__(self, meta):
        self.size, nt, npf, ncb, nsp, nrc = struct.unpack_from('<6Q', meta, 0)
        self.local_memory, self.texture_bound = struct.unpack_from('<II', meta, 48)
        self.stage = struct.unpack_from('<I', meta, 72)[0]
        tail = meta[H.ENV_HDR + nt * 8 + npf * 8 + ncb * 12 + nsp * 9 + nrc * 12:]
        self.header = bytes(tail[:80]) if self.stage != COMPUTE else None     # native program header (sph)
        # task/mesh environments end with three mesh registers and the 20 word primitive header
        self.mesh_state = bytes(tail[-92:]) if self.stage in (TASK, MESH) and len(tail) >= 172 else None

    @property
    def stage_name(self):
        return STAGES.get(self.stage, f'stage{self.stage}')


def mesh_state_hash(envs):
    """ShaderCache::MeshStateHash() of a pipeline: CityHash64(3 mesh registers + 20 header words) | 1.
    The mesh environment holds the primitive header; a task environment only the registers."""
    for want in (MESH, TASK):
        for e in envs:
            if e.stage == want and e.mesh_state:
                return city_hash64(e.mesh_state) | 1
    return None


# ---- what came out of the game files -------------------------------------------------------------
class Work:
    """A build_code_store.py work folder: the store, the extractor's manifest and the lowering results."""

    def __init__(self, work, log):
        self.work, self.log = work, log
        store = os.path.join(work, 'store')
        self.gfx_dir, self.mesh_dir = os.path.join(store, 'gfx'), os.path.join(store, 'mesh')
        self.store = H.CodeStore(self.gfx_dir, self.mesh_dir, os.path.join(store, 'compute.bin'))
        self.gfx = {int(n[:16], 16) for n in os.listdir(self.gfx_dir) if n.endswith('.bin')}
        self.manifest = json.load(open(os.path.join(work, 'extracted', 'manifest.json'), encoding='utf-8'))
        self.programs = {p['code']: p for p in self.manifest['programs']}
        self.by_hash = collections.defaultdict(list)       # lowered hash -> programs
        self.failed = []                                   # (program, error text)
        self.never_lowered = [p for p in self.manifest['programs']
                              if p['stage'] != 'compute' and p.get('format') != 'native-header']
        self.by_member = collections.defaultdict(list)     # (source, member) -> [(variation, stage, program)]
        for p in self.manifest['programs']:
            for o in p['origins']:
                self.by_member[(o['source'], o['member'])].append((o.get('variation'), p['stage'], p))
        self.result = {}                                   # program code path -> ('OK', hash) | ('ERR', text)
        self.lowering_known = self._lowering()
        self._headers = {}

    def size(self, kind, h):
        """Code size of a stored shader, or None."""
        if kind == 'compute':
            c = self.store.compute.get(h)
            return len(c) if c is not None else None
        if h not in (self.gfx if kind == 'gfx' else self.store.mesh):
            return None
        return os.path.getsize(os.path.join(self.gfx_dir if kind == 'gfx' else self.mesh_dir, f'{h:016x}.bin'))

    def code_head(self, kind, h):
        with open(os.path.join(self.gfx_dir if kind == 'gfx' else self.mesh_dir, f'{h:016x}.bin'), 'rb') as f:
            return f.read(80)

    def native_header(self, program):
        """First 80 bytes of the extracted native file: the header the emulator records with the stage."""
        key = program['code']
        if key not in self._headers:
            try:
                with open(os.path.join(self.work, 'extracted', key), 'rb') as f:
                    self._headers[key] = f.read(80)
            except OSError:
                self._headers[key] = b''
        return self._headers[key]

    def control(self, program):
        """(workgroup, shared memory, local memory) of a compute program, as ReadComputeControl reads them."""
        try:
            c = open(os.path.join(self.work, 'extracted', program['control']), 'rb').read()
        except OSError:
            return None
        if len(c) < 108:
            return None
        wg = struct.unpack_from('<3I', c, 88)
        return wg, (struct.unpack_from('<I', c, 100)[0] + 255) // 256 * 256, struct.unpack_from('<I', c, 104)[0]

    def _lowering(self):
        """Run the lowering programs once more, only to keep what they print: which program gave which hash
        and why the others failed. build_code_store.py throws that away. Kept in <work>/diagnose-*.txt."""
        base, known = os.path.join(self.work, 'extracted'), True
        for name in ('gfx', 'mesh', 'compute'):
            tsv, keep = os.path.join(self.work, f'{name}.tsv'), os.path.join(self.work, f'diagnose-{name}.txt')
            tool = os.path.join(self.work, 'bin', ('compute_hash' if name == 'compute' else 'hash_dump') + EXE)
            if not os.path.exists(tsv):
                self.log(f'note: {tsv} is missing; lowering results of the {name} programs are unknown')
                known = False
                continue
            rows = [l.split('\t') for l in open(tsv, encoding='utf-8').read().splitlines() if l.strip()]
            if os.path.exists(keep):
                out = open(keep, encoding='utf-8').read().splitlines()
            elif not rows:
                out = []
            elif not os.path.exists(tool):
                self.log(f'note: {tool} is missing; lowering results of the {name} programs are unknown')
                known = False
                continue
            else:
                print(f'lowering the {len(rows)} {name} programs again to record the errors ...', flush=True)
                env = {k: v for k, v in os.environ.items() if k != 'DUMP_DIR'}       # nothing is written to the store
                r = subprocess.run([tool, tsv], env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
                out = [l for l in r.stdout.splitlines() if l.startswith(('OK\t', 'ERR\t'))]
                if r.returncode or len(out) != len(rows):
                    self.log(f'note: {os.path.basename(tool)} stopped after {len(out)} of {len(rows)} {name} programs '
                             f'(exit code {r.returncode}); the rest have no lowering result')
                else:
                    with open(keep, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(out) + '\n')
            # one output line per input line, in order (hash_dump prints the stage, not the path)
            for row, line in zip(rows, out):
                rel = os.path.relpath(row[0], base).replace(os.sep, '/')
                p = self.programs.get(rel)
                if p is None:
                    continue
                f = line.split('\t')
                if f[0] == 'OK':
                    h = int(f[2], 16)
                    self.result[rel] = ('OK', h)
                    self.by_hash[h].append(p)
                else:
                    self.result[rel] = ('ERR', f[-1])
                    self.failed.append((p, f[-1]))
        return known

    def state(self, program):
        """What happened to an extracted program, as text."""
        r = self.result.get(program['code'])
        if r is None:
            if program in self.never_lowered:
                return f'never lowered: the extractor marks it format={program.get("format")!r}, build_code_store.py only lowers "native-header"'
            return 'no lowering result'
        if r[0] == 'OK':
            return f'lowered to {r[1]:016x}'
        return f'LOWERING FAILED: {r[1]}'


def origin_text(program, limit=2):
    seen, out = set(), []
    for o in program['origins']:
        t = f'{os.path.basename(o["source"])} :: {o["member"]} variation {o.get("variation")}'
        if t not in seen:
            seen.add(t)
            out.append(t)
    more = f' (+{len(out) - limit} more places)' if len(out) > limit else ''
    return '; '.join(out[:limit]) + more


# ---- diagnosis -------------------------------------------------------------------------------------
def candidates(w, env, siblings):
    """Extracted programs that are probably the missing shader of `env`:
    1. partners: programs of the same stage next to a sibling shader that was found (same archive member, same
       variation in .bfsha, the neighbouring record in .sharcb);
    2. programs of that stage with the same 80 byte native header that failed to lower or were never lowered."""
    out, seen = [], set()

    def add(p, how):
        if p['code'] not in seen:
            seen.add(p['code'])
            out.append((how, p))

    for sib in siblings:
        for o in sib['origins']:
            var = o.get('variation')
            if var is None:
                continue
            for v, stage, p in w.by_member[(o['source'], o['member'])]:
                if stage == env.stage_name and v is not None and abs(v - var) <= 1 and p is not sib:
                    add(p, f'partner of the shader that was found (variation {var})')
    if env.header:
        for p, _ in w.failed:
            if p['stage'] == env.stage_name and w.native_header(p) == env.header:
                add(p, 'same native header')
        for p in w.never_lowered:
            if p['stage'] == env.stage_name and w.native_header(p) == env.header:
                add(p, 'same native header')
    # programs that failed or were never lowered explain a missing shader; the ones that lowered fine (to another
    # hash) are only shown when there is nothing else
    bad = [c for c in out if w.result.get(c[1]['code'], ('',))[0] != 'OK']
    return bad or out[:3]


def diagnose_graphics(ident, metas, w, importer_reason):
    """(cause, [slot dicts]) for a graphics pipeline the importer skips (w is None: predicted from the hints alone)."""
    h, _ = ident
    used = [(i, v) for i, v in enumerate(h) if v]
    if len(used) != len(metas):
        return COUNT, [dict(slot=SLOTS[i], keyed=v, note=f'{len(used)} hashes, {len(metas)} environments') for i, v in used]
    envs = [Env(m) for m in metas]
    x = mesh_state_hash(envs)
    slots, found = [], []
    for (i, v), e in zip(used, envs):
        s = dict(slot=SLOTS[i], stage=e.stage_name, keyed=v, size=e.size, env=e, real=v, where=None, have=None)
        if e.stage in (TASK, MESH):
            s['real'] = v ^ x if x is not None else None
            s['mesh_state'] = x
        if w is not None and s['real'] is not None:
            for kind in (('mesh', 'gfx') if e.stage in (TASK, MESH) else ('gfx', 'mesh')):
                n = w.size(kind, s['real'])
                if n is not None:
                    s['where'], s['have'] = kind, n
                    found += w.by_hash.get(s['real'], [])
                    break
        slots.append(s)
    has_mesh = any(e.stage in (TASK, MESH) for e in envs)
    if has_mesh and not (h[1] and h[3]):
        return MESH_ONLY, slots
    if w is None:
        return None, slots                               # the importer handles this shape; needs the store
    for s in slots:
        if s['where'] is None:
            s['candidates'] = candidates(w, s['env'], found)
    if any(s['where'] is None for s in slots):
        return NOT_REPRODUCED, slots
    if any(s['have'] != s['size'] for s in slots) or importer_reason == 'shader size differs':
        return SIZE, slots
    return (MESH_STATE if has_mesh else NOT_REPRODUCED), slots


def diagnose_compute(key, metas, w):
    h, shared, *wg = struct.unpack_from('<QI3I', key, 0)
    s = dict(slot='Compute', stage='compute', keyed=h, real=h, where=None, have=None,
             note=f'workgroup {wg[0]}x{wg[1]}x{wg[2]}, shared memory {shared}')
    if len(metas) != 1:
        return COUNT, [dict(s, note=f'{len(metas)} environments for a compute pipeline')]
    e = Env(metas[0])
    s.update(size=e.size, env=e)
    if w is None:
        return None, [s]
    n = w.size('compute', h)
    if n is not None:
        s['where'], s['have'] = 'compute', n
        return COMPUTE_SIZE, [s]
    cands = []
    for p in w.manifest['programs']:
        if p['stage'] != 'compute' or w.result.get(p['code'], ('', 0))[0] == 'OK' and w.result[p['code']][1] == h:
            continue
        c = w.control(p)
        if c and c[0] == tuple(wg) and c[1] == shared and c[2] == e.local_memory:
            cands.append(('same workgroup, shared and local memory', p))
    s['candidates'] = cands
    return COMPUTE_MISSING, [s]


def why_slot(s, w, cause, sizes=True):
    """One line: what is wrong with this slot."""
    if 'note' in s and 'stage' not in s:
        return s['note']
    if s.get('real') is None:
        return 'mesh state hash could not be computed from the recorded environment'
    if w is None:
        return 'not checked (no code store)'
    if s['where'] is None:
        return 'NOT in the code store'
    t = f'in the {s["where"]} store'
    if s['have'] != s['size']:
        return t + (f', but with {s["have"]} bytes instead of the recorded {s["size"]}' if sizes else ', but with another code size')
    if cause == MESH_ONLY and s['stage'] == 'mesh':
        return t + ' under its real hash: the code exists, only the importer does not look there'
    return t


# ---- scan of the game file -------------------------------------------------------------------------
BULKY = {'Tex', 'TerrainArc', 'Sound', 'Bake', 'Phive', 'PreComputedCulling', 'Voice', 'Movie', 'Model', 'Foliage', 'Event'}


def _sarc(data):
    if data[:4] != b'SARC' or data[6:8] != b'\xff\xfe':
        return
    header, _, start = struct.unpack_from('<HxxII', data, 4)
    count = struct.unpack_from('<H', data, header + 6)[0]
    nodes = header + 12
    names = nodes + count * 16 + 8
    for i in range(count):
        _, flags, s, e = struct.unpack_from('<4I', data, nodes + i * 16)
        at = names + (flags & 0xffffff) * 4
        yield data[at:data.index(b'\0', at)].decode('utf-8', 'replace'), data[start + s:start + e]


def _containers(data):
    """Shader container magics inside data, with the header checks the extractor makes (cuts chance matches)."""
    out = collections.Counter()
    out['BNSH'] = data.count(b'BNSH\0\0\0\0')
    for magic, check in ((b'FSHA', lambda d, o: d[o + 12:o + 14] == b'\xff\xfe'),
                         (b'BAHS', lambda d, o: d[o + 4:o + 8] == b'\x0b\0\0\0'),
                         (b'NDSR', lambda d, o: True)):
        o = data.find(magic)
        while o >= 0:
            out[magic.decode()] += check(data, o)
            o = data.find(magic, o + 4)
    return +out


def scan_nup(path, everything, log):
    """Files in the .nup that hold shader containers but are not read by build_code_store.py."""
    from nup_read import Nup, _zstd_module
    n = Nup(path)
    files = {}
    for pk in sorted(range(len(n.packages)), key=lambda i: n.packages[i]['kind']):
        files.update(n.walk(n.packages[pk]['root']))
    read = [p for p in files if B.PATTERN.match(p)]
    log(f'{len(files)} files in the .nup; build_code_store.py reads {len(read)} of them '
        f'({", ".join(f"{k} {v}" for k, v in collections.Counter(p.split("/")[1] for p in read).most_common())})')
    z = _zstd_module()
    dicts = {}
    if z is None:
        log('note: no zstd module (Python 3.14 or backports.zstd); compressed files are not scanned')
    elif 'romfs/Pack/ZsDic.pack.zs' in files:
        try:
            for _, blob in _sarc(z.decompress(n.read(files['romfs/Pack/ZsDic.pack.zs']))):
                d = z.ZstdDict(blob)
                dicts[d.dict_id] = d
        except Exception as ex:
            log(f'note: cannot read the zstd dictionaries ({ex})')
    hits, scanned, skipped, unreadable = [], 0, collections.Counter(), 0
    for p, fi in files.items():
        if B.PATTERN.match(p):
            continue
        top = p.split('/')[1] if p.startswith('romfs/') and p.count('/') > 1 else p.split('/')[0]
        if top in BULKY and not everything:
            skipped[top] += 1
            continue
        data = n.read(fi)
        if data[:4] == b'\x28\xb5\x2f\xfd':
            if z is None:
                unreadable += 1
                continue
            try:
                data = z.decompress(data, zstd_dict=dicts.get(z.get_frame_info(data).dictionary_id))
            except Exception:
                unreadable += 1
                continue
        scanned += 1
        found = _containers(data)
        if found:
            hits.append((p, found))
    log(f'scanned {scanned} other files for shader containers (BNSH, FSHA, BAHS, NDSR); {unreadable} could not be decompressed')
    if skipped:
        log(f'not scanned (large asset folders, use --scan-all): {", ".join(f"{k} {v}" for k, v in skipped.most_common())}')
    if hits:
        log(f'{len(hits)} files hold shader containers that build_code_store.py does NOT read:')
        for p, found in hits:
            log(f'  {p}: {", ".join(f"{k} x{v}" for k, v in found.items())}')
        log('  (a single FSHA/BAHS/NDSR hit inside texture or audio data can be a chance match)')
    else:
        log('no shader container was found outside the files build_code_store.py reads')
    return hits


# ---- report ----------------------------------------------------------------------------------------
def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hints', default=os.path.join(ROOT, 'hints', 'combined.hints'), help='hints file (default: the bundled one)')
    ap.add_argument('--nup', help='the game .nup: scanned for shader containers, and used to build --work when that does not exist')
    ap.add_argument('--work', help='work folder of build_code_store.py (or of scb_oot.py --keep-work: <temp>/work). '
                    'Built from --nup when it does not exist; it holds game code, keep it local')
    ap.add_argument('--lichii', default=os.path.join(ROOT, 'lichii'), help='Lichii clone, only to build --work')
    ap.add_argument('--bin-dir', default=os.path.join(ROOT, 'bin'), help='ready-made hash_dump/compute_hash, only to build --work')
    ap.add_argument('--extractor-python', default=sys.executable)
    ap.add_argument('--out', default=os.path.join(ROOT, 'out'), help='folder for missing_shaders.log and .tsv (default: out/)')
    ap.add_argument('--no-scan', action='store_true', help='do not scan the .nup for shader containers')
    ap.add_argument('--scan-all', action='store_true', help='also scan the large asset folders of the .nup (slow)')
    ap.add_argument('--max-list', type=int, default=40, help='shaders listed per cause on the console (the log has all)')
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    log_path, tsv_path = os.path.join(a.out, 'missing_shaders.log'), os.path.join(a.out, 'missing_shaders.tsv')
    logf = open(log_path, 'w', encoding='utf-8')

    def log(msg='', console=True):
        logf.write(msg + '\n')
        if console:
            print(msg, flush=True)

    log(f'diagnose_missing.py, {datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
    head, records, ages = H.load(a.hints)
    ng = sum(1 for k in records if k[0] == 'g')
    log(f'hints: {a.hints}\n  title {head.get("title_id")}, cache version {head.get("cache_version")}, '
        f'{ng} graphics + {len(records) - ng} compute pipelines')

    # the code store
    w = None
    if a.work:
        a.work = os.path.abspath(a.work)
        if not os.path.exists(a.work):
            if not a.nup:
                sys.exit(f'{a.work} does not exist; give --nup to build it')
            print(f'building the code store in {a.work} (about a minute) ...', flush=True)
            B.build_store(os.path.abspath(a.nup), a.work, os.path.abspath(a.lichii), os.path.abspath(a.bin_dir),
                          a.extractor_python, head.get('title_id') or '0' * 16)
        if not os.path.isdir(os.path.join(a.work, 'store', 'gfx')) or not os.path.exists(os.path.join(a.work, 'extracted', 'manifest.json')):
            sys.exit(f'{a.work} is not a finished build_code_store.py work folder (store/gfx or extracted/manifest.json is missing)')
        w = Work(a.work, log)
        log(f'code store: {a.work}\n  {len(w.gfx)} graphics, {len(w.store.mesh)} task/mesh, {len(w.store.compute)} compute shaders; '
            f'{len(w.manifest["programs"])} extracted programs, {len(w.failed)} failed to lower, '
            f'{len(w.never_lowered)} never lowered, {len(w.manifest["errors"])} unreadable containers')
    else:
        log('code store: none (--work not given). Only what the hints alone show is reported.')

    # 1. which pipelines are skipped, and why
    skipped = []                                           # (index, kind, ident, cause, slots)
    undecided = 0
    if w is not None:
        verdicts = H.rebuild(records, w.store, (0, 0, 0))
    else:
        verdicts = ((None, None, k) for k in records)
    for index, (data, reason, (kind, ident)) in enumerate(verdicts):
        if data is not None:
            continue
        metas = records[(kind, ident)]
        cause, slots = diagnose_graphics(ident, metas, w, reason) if kind == 'g' else diagnose_compute(ident, metas, w)
        if cause is None:                                  # no store: the importer handles this shape
            undecided += 1
            continue
        skipped.append((index, kind, ident, cause, slots))

    log()
    log('=' * 100)
    if w is not None:
        log(f'RESULT: the importer rebuilds {len(records) - len(skipped)} of {len(records)} pipelines and skips {len(skipped)}.')
    else:
        log(f'RESULT (hints only): {len(skipped)} of {len(records)} pipelines are certain to be skipped because of their shape.\n'
            f'The other {undecided} depend on the code store: {UNKNOWN}.')
    by_cause = collections.defaultdict(list)
    for row in skipped:
        by_cause[row[3]].append(row)
    for cause in (MESH_ONLY, MESH_STATE, NOT_REPRODUCED, SIZE, COUNT, COMPUTE_MISSING, COMPUTE_SIZE):
        if cause in by_cause:
            log(f'  {len(by_cause[cause]):>6}  {cause}')
    log('=' * 100)

    # 2. per cause: the distinct shaders that block pipelines
    for cause in (MESH_ONLY, MESH_STATE, NOT_REPRODUCED, SIZE, COUNT, COMPUTE_MISSING, COMPUTE_SIZE):
        rows = by_cause.get(cause)
        if not rows:
            continue
        log()
        log(f'--- {len(rows)} pipelines: {cause}')
        if cause == MESH_ONLY:
            states = {s.get('mesh_state') for r in rows for s in r[4] if s.get('stage') == 'mesh'}
            log('    The emulator puts a mesh shader that has no task shader into the VertexB slot and XORs its hash with\n'
                '    MeshStateHash (shader_cache.cpp). hints.rebuild() only undoes that XOR when both slot 1 (task) and\n'
                '    slot 3 (mesh) are set, so here it looks for the keyed hash among the vertex shaders and finds nothing.\n'
                f'    These pipelines use {len(states)} mesh states; the real hash below is keyed hash ^ MeshStateHash.')
        blockers = collections.OrderedDict()               # (stage, real or keyed hash) -> info
        for index, kind, ident, _, slots in rows:
            age = ages.get((kind, ident), H.UNKNOWN_AGE)
            for s in slots:
                bad = (s.get('where') is None or s.get('have') != s.get('size')) if w is not None else s.get('stage') in ('mesh', 'task')
                if cause == MESH_ONLY:
                    bad = s.get('stage') == 'mesh'
                if not bad:
                    continue
                key = (s.get('stage', '?'), s['real'] if s.get('real') is not None else s['keyed'])
                b = blockers.setdefault(key, dict(s=s, pipelines=0, keyed=set(), age=H.UNKNOWN_AGE))
                b['pipelines'] += 1
                b['keyed'].add(s['keyed'])
                b['age'] = min(b['age'], age)
        order = sorted(blockers.items(), key=lambda kv: -kv[1]['pipelines'])
        status = collections.Counter(why_slot(b['s'], w, cause, False) for _, b in order)
        log(f'    {len(order)} distinct shaders block them:')
        for text, n in status.most_common():
            log(f'      {n:>5} shaders: {text}')
        for rank, ((stage, hsh), b) in enumerate(order):
            s, console = b['s'], rank < a.max_list
            age = 'never seen in a recorded session list' if b['age'] == H.UNKNOWN_AGE else f'last used {b["age"]} sessions ago'
            log(f'    [{stage:<8}] real hash {hsh:016x}  code {s.get("size", "?")} bytes  blocks {b["pipelines"]} pipelines '
                f'under {len(b["keyed"])} keyed hashes  ({age})', console)
            log(f'        {why_slot(s, w, cause)}' + (f'; {s["note"]}' if 'note' in s and 'stage' in s else ''), console)
            if w is not None and s.get('where') is None:
                cands = s.get('candidates') or []
                if not cands:
                    log('        no extracted program matches: the shader is in a container the extractor cannot read, is not in\n'
                        '        the files that were read, or the emulator lowers it with values only known while the game runs', console)
                for how, p in cands[:6]:
                    log(f'        candidate ({how}): {origin_text(p)}', console)
                    log(f'            -> {w.state(p)}', console)
                if len(cands) > 6:
                    log(f'        ... and {len(cands) - 6} more candidates', console)
        if len(order) > a.max_list:
            print(f'    ... {len(order) - a.max_list} more shaders in {log_path}', flush=True)

    # 3. what went wrong while reading the game files
    if w is not None:
        log()
        log('--- game files: extraction and lowering')
        errs = collections.defaultdict(list)
        for e in w.manifest['errors']:
            errs[e.get('error', '?')].append(e.get('member') or os.path.basename(e.get('path', '?')))
        log(f'    {len(w.manifest["errors"])} containers the extractor could not read:')
        for text, names in sorted(errs.items(), key=lambda kv: -len(kv[1])):
            log(f'      {len(names):>5} x {text}')
            for nm in names[:5]:
                log(f'              {nm}')
            for nm in names[5:]:
                log(f'              {nm}', False)
        fails = collections.Counter((err, p['stage']) for p, err in w.failed)
        log(f'    {len(w.failed)} extracted programs that LowerProgram rejected' + ('' if w.lowering_known else ' (incomplete, see notes above)') + ':')
        for (err, stage), n in fails.most_common():
            log(f'      {n:>5} x {stage:<9} {err}')
        for p, err in w.failed:
            log(f'              {p["stage"]:<9} {err}: {origin_text(p, 1)}', False)
        if w.never_lowered:
            c = collections.Counter(p['stage'] for p in w.never_lowered)
            log(f'    {len(w.never_lowered)} extracted graphics programs build_code_store.py never lowers (format is not '
                f'"native-header"): {", ".join(f"{k} {v}" for k, v in c.most_common())}')
        # sanity check of the header matching used for candidates
        same = total = 0
        for (kind, ident), metas in records.items():
            if kind != 'g' or total >= 2000:
                continue
            for v, m in zip([x for x in ident[0] if x], metas):
                e = Env(m)
                if e.stage not in (TASK, MESH) and v in w.gfx:
                    total += 1
                    same += w.code_head('gfx', v) == e.header
        if total:
            log(f'    check: the recorded native header equals the first 80 code bytes for {same} of {total} shaders that were found'
                + ('' if same == total else '  <-- candidates matched by header are unreliable'))

    # 4. the game file itself
    if a.nup and not a.no_scan:
        log()
        log(f'--- game file: {a.nup}')
        scan_nup(a.nup, a.scan_all, log)

    # 5. one row per skipped pipeline
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write('record\tkind\tcause\tage\tmesh_state_hash\tslots (slot:stage:keyed_hash:real_hash:recorded_size:store:store_size)\n')
        for index, kind, ident, cause, slots in skipped:
            age = ages.get((kind, ident), '')
            x = next((s['mesh_state'] for s in slots if s.get('mesh_state') is not None), None)
            cells = []
            for s in slots:
                real = f'{s["real"]:016x}' if s.get('real') is not None else '?'
                cells.append(f'{s["slot"]}:{s.get("stage", "?")}:{s["keyed"]:016x}:{real}:{s.get("size", "?")}:'
                             f'{s.get("where") or ("-" if w is not None else "?")}:{s.get("have") if s.get("have") is not None else "-"}')
            f.write(f'{index}\t{"graphics" if kind == "g" else "compute"}\t{cause}\t{age}\t'
                    f'{f"{x:016x}" if x is not None else ""}\t{" ".join(cells)}\n')
    log()
    log(f'full report: {log_path}\nper pipeline:  {tsv_path}')
    logf.close()


if __name__ == '__main__':
    try:
        main()
    except (H.HintError, cc.CacheError) as ex:
        sys.exit(f'error: {ex}')
