#!/usr/bin/env python3
"""Code-free "hints" for Yuzu-lineage Vulkan pipeline caches (Lichii, Eden; cache version 54).

A hints file keeps everything of a pipeline cache except the shader code: the pipeline keys (draw state
and shader hashes), the recorded environments (headers and the values the translator read) and nothing
GPU specific. It is a few MB instead of GBs and contains no game code.

  export   cache  -> hints         (run on a cache your emulator wrote, any GPU)
  merge    hints+ -> hints         (union of several hints files)
  info     hints                   (what is inside)
  import   hints + shader code from YOUR game files -> a cache for YOUR GPU

The importer re-creates every record whose shaders it can regenerate from the game files and stamps
the keys with this machine's driver (see convert_cache.py for --detect-system and the manual options).
It never modifies an input and never overwrites an output.
"""
import argparse, lzma, mmap, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_cache as cc
from cityhash import city_hash64

MAGIC = b'SHHINT02'
STATE = cc.STATE
STATE_SIZE = 1008
DRIVER_WORDS = (16, 24)                    # driver id + version inside the state
ENV_HDR = 76


class HintError(Exception):
    pass


UNKNOWN_AGE = 255
USED_MAGIC = 0x31505545                                   # "EUP1", vulkan_used.fork-v54.bin


def hashed_state_size(raw1):
    """Bytes of FixedPipelineState that GraphicsPipelineCacheKey::Hash() covers (FixedPipelineState::Size())."""
    if raw1 >> 6 & 1:                                    # transform feedback: the whole struct
        return 1008
    if raw1 >> 5 & 1 and raw1 >> 3 & 1:                  # dynamic vertex input and dynamic blend state 3
        return 80
    if raw1 >> 5 & 1:                                    # dynamic vertex input
        return 112
    if raw1 & 1:                                         # extended dynamic state
        return 368
    return 432


def key_hash(key):
    """The pipeline hash Lichii keeps in vulkan_used.fork-v54.bin (verified on 2,285 of 2,285 entries)."""
    return city_hash64(key[:48 + hashed_state_size(struct.unpack_from('<I', key, 48)[0])])


def read_used_list(path):
    d = open(path, 'rb').read()
    magic, n = struct.unpack_from('<II', d, 0)
    if magic != USED_MAGIC:
        raise HintError(f'{path} is not a used-pipelines list')
    return {struct.unpack_from('<Q', d, 8 + i * 9)[0]: d[16 + i * 9] for i in range(n)}


def normalize_state(state):
    """State without the machine specific parts (driver id/version, dynamic-state feature bits)."""
    s = bytearray(state)
    s[DRIVER_WORDS[0]:DRIVER_WORDS[1]] = bytes(8)
    raw1 = struct.unpack_from('<I', s, 0)[0] & ~cc.FEATURE_MASK
    struct.pack_into('<I', s, 0, raw1)
    return bytes(s)


def split_record(m, off, eend, end, compute):
    """(kind, key fields, [env meta blobs]) of the record at off. A meta blob is the 76 byte env header
    followed by everything after the shader code (tables, reads, program header)."""
    n = struct.unpack_from('<I', m, off)[0]
    o, metas = off + 4, []
    for _ in range(n):
        size = struct.unpack_from('<Q', m, o)[0]
        nxt, _ = cc.env_end(m, o)
        metas.append(bytes(m[o:o + ENV_HDR]) + bytes(m[o + ENV_HDR + size:nxt]))
        o = nxt
    key = bytes(m[eend:end])
    if compute:
        return ('c', key, metas)
    return ('g', (struct.unpack_from('<6Q', key, 0), normalize_state(key[STATE:])), metas)


def read_cache(path, version=54, only_driver=None, used=None, age_offset=0):
    """(records, ages): ages[(kind, ident)] = sessions since the pipeline was used, from the machine's used list."""
    f = open(path, 'rb')
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    out, ages = {}, {}
    for off, eend, end, compute in cc.read_records(m, version):
        if only_driver is not None and not compute and struct.unpack_from('<I', m, eend + cc.DRIVER_ID)[0] != only_driver:
            continue                                      # keep only records written on that driver
        kind, ident, metas = split_record(m, off, eend, end, compute)
        out[(kind, ident)] = metas                    # the newest record of a pipeline wins, as in convert_cache.py
        if used is not None and kind == 'g':
            age = used.get(key_hash(bytes(m[eend:end])))
            if age is not None:
                ages[(kind, ident)] = min(ages.get((kind, ident), UNKNOWN_AGE), min(UNKNOWN_AGE - 1, age + age_offset))
    return out, ages


# ---- file format -------------------------------------------------------------------------------
def dump(records, title, version, out_path, note='', ages=None):
    states, metas, meta_ix = {}, [], {}
    for (kind, ident), ms in records.items():
        if kind == 'g':
            states.setdefault(ident[1], len(states))
        for b in ms:
            if b not in meta_ix:
                meta_ix[b] = len(metas)
                metas.append(b)
    slist = list(states)
    var = [i for i in range(STATE_SIZE // 4)
           if len({s[i * 4:i * 4 + 4] for s in slist}) > 1]
    body = bytearray()
    body += struct.pack('<IIII', len(slist), len(metas), len(records), len(var))
    body += slist[0] if slist else bytes(STATE_SIZE)
    body += struct.pack(f'<{len(var)}H', *var)
    for s in slist:
        body += b''.join(s[i * 4:i * 4 + 4] for i in var)
    for b in metas:
        body += struct.pack('<I', len(b)) + b
    for (kind, ident), ms in records.items():
        if kind == 'g':
            h, st = ident
            body += b'g' + struct.pack('<B6QH', len(ms), *h, states[st])
        else:
            body += b'c' + struct.pack('<B', len(ms)) + ident
        body += struct.pack(f'<{len(ms)}I', *[meta_ix[b] for b in ms])
    body += bytes((ages or {}).get(k, UNKNOWN_AGE) for k in records)     # one age per record, in record order
    header = f'{{"schema":"shader-hints-1","title_id":"{title}","cache_version":{version},"records":{len(records)},"note":"{note}"}}'.encode()
    with open(out_path, 'xb') as f:
        f.write(MAGIC + struct.pack('<I', len(header)) + header + lzma.compress(bytes(body), preset=9 | lzma.PRESET_EXTREME))


def load(path):
    d = open(path, 'rb').read()
    if d[:8] != MAGIC:
        raise HintError(f'{path} is not a hints file')
    hl = struct.unpack_from('<I', d, 8)[0]
    import json
    head = json.loads(d[12:12 + hl])
    body = lzma.decompress(d[12 + hl:])
    ns, nm, nr, nv = struct.unpack_from('<IIII', body, 0)
    o = 16
    template = body[o:o + STATE_SIZE]; o += STATE_SIZE
    var = struct.unpack_from(f'<{nv}H', body, o); o += 2 * nv
    states = []
    for _ in range(ns):
        s = bytearray(template)
        for i, w in zip(var, struct.unpack_from(f'<{nv}I', body, o)):
            struct.pack_into('<I', s, i * 4, w)
        o += 4 * nv
        states.append(bytes(s))
    metas = []
    for _ in range(nm):
        n = struct.unpack_from('<I', body, o)[0]; o += 4
        metas.append(body[o:o + n]); o += n
    records = {}
    for _ in range(nr):
        kind = body[o:o + 1]; o += 1
        if kind == b'g':
            n, *rest = struct.unpack_from('<B6QH', body, o); o += 1 + 48 + 2
            ident = (tuple(rest[:6]), states[rest[6]])
            key = ('g', ident)
        else:
            n = body[o]; o += 1
            key = ('c', body[o:o + cc.KEY_COMPUTE]); o += cc.KEY_COMPUTE
        ix = struct.unpack_from(f'<{n}I', body, o); o += 4 * n
        records[key] = [metas[i] for i in ix]
    ages = {k: body[o + i] for i, k in enumerate(records) if body[o + i] != UNKNOWN_AGE}
    return head, records, ages


# ---- shader code from the game files -----------------------------------------------------------
class CodeStore:
    """Lowered shader code by hash: graphics and mesh/task as <hash>.bin files, compute from a file of
    generated compute records (see harness/ in the project)."""

    def __init__(self, gfx_dir, mesh_dir, compute_file, version=54):
        self.gfx_dir, self.mesh_dir = gfx_dir, mesh_dir
        self.mesh, self.task, self.meshstage = set(), set(), set()
        if mesh_dir:
            for n in os.listdir(mesh_dir):
                if n.endswith('.bin'):
                    self.mesh.add(int(n[:16], 16))
            index = os.path.join(mesh_dir, 'index.tsv')          # <hash> TAB task|mesh
            if os.path.exists(index):
                for line in open(index):
                    hsh, stage = line.split()
                    (self.task if stage == 'task' else self.meshstage).add(int(hsh, 16))
        self.compute = {}
        if compute_file:
            f = open(compute_file, 'rb')
            m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            for off, eend, end, comp in cc.read_records(m, version):
                if comp:
                    size = struct.unpack_from('<Q', m, off + 4)[0]
                    self.compute[struct.unpack_from('<Q', m, eend)[0]] = bytes(m[off + 4 + ENV_HDR:off + 4 + ENV_HDR + size])

    def gfx(self, h):
        p = os.path.join(self.gfx_dir, f'{h:016x}.bin') if self.gfx_dir else None
        return open(p, 'rb').read() if p and os.path.exists(p) else None

    def meshcode(self, h):
        return open(os.path.join(self.mesh_dir, f'{h:016x}.bin'), 'rb').read() if h in self.mesh else None


def rebuild(records, store, machine):
    """Yield (record bytes, why, identity) for every record; None bytes when the shaders cannot be regenerated."""
    for (kind, ident), metas in records.items():
        if kind == 'c':
            code = store.compute.get(struct.unpack_from('<Q', ident, 0)[0])
            if code is None or len(metas) != 1 or struct.unpack_from('<Q', metas[0], 0)[0] != len(code):
                yield None, 'compute shader not in game files', (kind, ident)
                continue
            yield struct.pack('<I', 1) + metas[0][:ENV_HDR] + code + metas[0][ENV_HDR:] + ident, 'ok', (kind, ident)
            continue
        h, state = ident
        slots = [x for x in h if x]
        codes = None
        if h[1] and h[3] and store.mesh:                 # mesh pipeline: slots hold hash ^ X
            ms, ts = (store.meshstage, store.task) if store.task else (store.mesh, store.mesh)
            for t in ms:
                x = h[3] ^ t
                if (h[1] ^ x) in ts:
                    codes = [store.meshcode(v ^ x) if i in (1, 3) else store.gfx(v) for i, v in enumerate(h) if v]
                    break
        else:
            codes = [store.gfx(v) for v in slots]
        if codes is None or any(c is None for c in codes) or len(codes) != len(metas):
            yield None, 'shader not in game files', (kind, ident)
            continue
        if any(struct.unpack_from('<Q', mt, 0)[0] != len(c) for mt, c in zip(metas, codes)):
            yield None, 'shader size differs', (kind, ident)
            continue
        key = struct.pack('<6Q', *h) + b'\0' * 0
        key = struct.pack('<6Q', *h) + cc.stamp(bytes(STATE) + state, machine)[STATE:]
        body = struct.pack('<I', len(metas)) + b''.join(mt[:ENV_HDR] + c + mt[ENV_HDR:] for mt, c in zip(metas, codes))
        yield body + key, 'ok', (kind, ident)


# ---- command line ------------------------------------------------------------------------------
def machine_args(ap):
    g = ap.add_argument_group('machine stamp (see convert_cache.py)')
    g.add_argument('--detect-system', action='store_true')
    g.add_argument('--vulkaninfo-file')
    g.add_argument('--vulkaninfo-cmd')
    g.add_argument('--gpu', type=int, default=0)
    g.add_argument('--dyna-state', type=int, choices=range(4), default=2)
    g.add_argument('--no-vids', action='store_true')
    g.add_argument('--driver-id', type=int)
    g.add_argument('--driver-version', type=lambda x: int(x, 0))
    g.add_argument('--features', type=lambda x: int(x, 0))


def machine_from_args(a):
    if a.driver_id is not None or a.driver_version is not None or a.features is not None:
        if None in (a.driver_id, a.driver_version, a.features):
            raise HintError('--driver-id, --driver-version and --features must be given together')
        return (a.driver_id, a.driver_version, a.features & cc.FEATURE_MASK), 'given by hand'
    if a.detect_system or a.vulkaninfo_file:
        text = open(a.vulkaninfo_file, encoding='utf-8', errors='replace').read() if a.vulkaninfo_file \
            else cc.run_vulkaninfo(a.vulkaninfo_cmd)
        return cc.machine_from_vulkaninfo(text, a.gpu, a.dyna_state, not a.no_vids)
    raise HintError('give the machine stamp: --detect-system, --vulkaninfo-file or the manual options')


def run_import(hints_path, out_path, gfx_dir, mesh_dir, compute_file, version, machine, used_list_out=None, fast_start_age=0):
    """Rebuild a cache (and optionally a starter used list) from a hints file and the game's lowered shader code.
    Returns (rebuilt, total, skipped reasons, starter list size)."""
    if os.path.exists(out_path):
        raise HintError(f'refusing to overwrite {out_path}')
    if used_list_out and os.path.exists(used_list_out):
        raise HintError(f'refusing to overwrite {used_list_out}')
    head, records, ages = load(hints_path)
    store = CodeStore(gfx_dir, mesh_dir, compute_file, version)
    ok, why, starter = 0, {}, {}
    with open(out_path, 'xb') as out:
        out.write(cc.MAGIC + struct.pack('<I', version))
        for data, reason, ident in rebuild(records, store, machine):
            if data is None:
                why[reason] = why.get(reason, 0) + 1
            else:
                out.write(data); ok += 1
                if used_list_out and ident[0] == 'g' and ages.get(ident, UNKNOWN_AGE) <= fast_start_age:
                    starter[key_hash(data[-cc.KEY_GRAPHICS:])] = ages[ident]
    if used_list_out:
        with open(used_list_out, 'xb') as f:
            f.write(struct.pack('<II', USED_MAGIC, len(starter)))
            for h, age in starter.items():
                f.write(struct.pack('<QB', h, age))
        print(f'starter used list: {len(starter)} pipelines (age <= {fast_start_age}) -> {used_list_out}')
    print(f'rebuilt {ok} of {len(records)} pipelines ({os.path.getsize(out_path) / 1e6:.0f} MB); skipped: {why}')
    return ok, len(records), why, len(starter)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')            # Windows consoles cannot print every character
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export'); e.add_argument('cache'); e.add_argument('--title', required=True)
    e.add_argument('--out', required=True); e.add_argument('--version', type=int, default=54)
    e.add_argument('--note', default='')
    e.add_argument('--used-list', help='the emulator\'s vulkan_used.fork-v54.bin of the same machine: records which pipelines a play session used')
    e.add_argument('--age-offset', type=int, default=0, help='added to the ages from --used-list (older recordings rank later)')
    e.add_argument('--only-driver-id', type=int, help='export only graphics records written by this VkDriverId (1 AMD, 4 NVIDIA)')
    mg = sub.add_parser('merge'); mg.add_argument('hints', nargs='+'); mg.add_argument('--out', required=True)
    mg.add_argument('--note', default='')
    i = sub.add_parser('info'); i.add_argument('hints')
    im = sub.add_parser('import'); im.add_argument('hints'); im.add_argument('--out', required=True)
    im.add_argument('--gfx-dir', required=True, help='folder of <hash>.bin lowered graphics shaders from your game files')
    im.add_argument('--mesh-dir', help='same for task and mesh shaders')
    im.add_argument('--compute-file', help='generated compute records (cache format) from your game files')
    im.add_argument('--version', type=int, default=54)
    im.add_argument('--used-list-out', help='also write a starter vulkan_used.fork-v54.bin (fast first boot, see README)')
    im.add_argument('--fast-start-age', type=int, default=0,
                    help='with --used-list-out: include pipelines whose recorded age is at most this (0 = the contributor\'s last session)')
    machine_args(im)
    a = ap.parse_args()

    if a.cmd == 'export':
        used = read_used_list(a.used_list) if a.used_list else None
        recs, ages = read_cache(a.cache, a.version, a.only_driver_id, used, a.age_offset)
        dump(recs, a.title.lower(), a.version, a.out, a.note, ages)
        print(f'exported {len(recs)} pipelines to {a.out} ({os.path.getsize(a.out) / 1e6:.2f} MB)'
              + (f'; {len(ages)} have a usage age' if used is not None else ''))
    elif a.cmd == 'merge':
        allr, allages, ver, title = {}, {}, None, None
        for p in a.hints:
            head, r, ages = load(p)
            for k, v in ages.items():
                allages[k] = min(allages.get(k, UNKNOWN_AGE), v)
            ver, title = head['cache_version'], head['title_id']
            before = len(allr)
            for k, v in r.items():
                allr.setdefault(k, v)
            print(f'{p}: {len(r)} pipelines, {len(allr) - before} new')
        dump(allr, title, ver, a.out, a.note, allages)
        print(f'wrote {len(allr)} pipelines to {a.out} ({os.path.getsize(a.out) / 1e6:.2f} MB)')
    elif a.cmd == 'info':
        head, r, ages = load(a.hints)
        g = sum(1 for k in r if k[0] == 'g')
        print(head); print(f'graphics {g}, compute {len(r) - g}')
        by_age = {}
        for v in ages.values():
            by_age[v] = by_age.get(v, 0) + 1
        print('pipelines by usage age (sessions since used, lower = used more recently):', dict(sorted(by_age.items())) or 'none recorded')
    else:
        machine, desc = machine_from_args(a)
        print(f'machine stamp: driver_id={machine[0]} driver_version={machine[1]:#x} features={machine[2]:#x} ({desc})')
        run_import(a.hints, a.out, a.gfx_dir, a.mesh_dir, a.compute_file, a.version, machine,
                   a.used_list_out, a.fast_start_age)


if __name__ == '__main__':
    try:
        main()
    except (HintError, cc.CacheError) as ex:
        sys.exit(f'error: {ex}')
