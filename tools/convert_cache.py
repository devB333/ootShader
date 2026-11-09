#!/usr/bin/env python3
"""Convert a Yuzu-lineage (Eden/Lichii) Vulkan pipeline cache written on other GPUs/drivers
so that the running machine accepts it.

Every graphics record's key ends with a FixedPipelineState that is stamped with the driver id, the
driver version and a few feature bits of the machine that wrote it. A cache written elsewhere
therefore has other keys and is ignored. This tool copies the stamp of THIS machine (taken from
the last graphics record of the cache you point it at, i.e. what your own emulator wrote) into the
other records and writes only those your machine does not have yet.

The stamp can also be read from the system's Vulkan driver (--detect-system), so you do not need a cache
written by your own emulator. It never modifies its inputs. Run: python3 -I convert_cache.py --help
"""
import argparse, collections, mmap, os, struct, sys

MAGIC = b'yuzucach'
KEY_GRAPHICS = 1056            # 6 x u64 shader hashes + 1008 byte FixedPipelineState
KEY_COMPUTE = 24
STATE = 48                     # state offset inside a graphics key
DRIVER_ID, DRIVER_VERSION = STATE + 16, STATE + 20
FEATURE_MASK = 0x3F | (1 << 20)   # raw1: extended dynamic state flags, dynamic vertex input, color write enable
STAGE_COMPUTE, STAGE_GEOMETRY, STAGE_TASK, STAGE_MESH = 5, 3, 7, 8


class CacheError(Exception):
    pass


def env_end(m, o):
    """Offset just after the serialized guest environment starting at o (see GenericEnvironment::Serialize)."""
    if o + 76 > len(m):
        raise CacheError(f'truncated environment at {o}')
    code, nt, npf, ncb, nsp, nrc = struct.unpack_from('<6Q', m, o)
    stage = struct.unpack_from('<I', m, o + 72)[0]
    if code > 0x200000 or max(nt, npf, ncb, nsp, nrc) > 100000 or stage > 12:
        raise CacheError(f'implausible environment at {o}')
    o += 76 + code + nt * 8 + npf * 8 + ncb * 12 + nsp * 9 + nrc * 12
    if stage == STAGE_COMPUTE:
        o += 16
    else:
        o += 0x50
        if stage == STAGE_GEOMETRY:
            o += 32
        if stage in (STAGE_TASK, STAGE_MESH):
            o += 12 + 0x50
    return o, stage


def read_records(m, version):
    """Yield (start, end_of_envs, end, is_compute) for every record."""
    if m[:8] != MAGIC or struct.unpack_from('<I', m, 8)[0] != version:
        raise CacheError(f'not a cache with magic {MAGIC!r} and version {version}')
    off = 12
    while off + 8 < len(m):
        n = struct.unpack_from('<I', m, off)[0]
        if not 1 <= n <= 6:
            raise CacheError(f'bad environment count at {off}')
        o, stage = off + 4, None
        for i in range(n):
            o, s = env_end(m, o)
            stage = s if stage is None else stage
        compute = stage == STAGE_COMPUTE
        end = o + (KEY_COMPUTE if compute else KEY_GRAPHICS)
        if end > len(m):
            raise CacheError(f'truncated record at {off}')
        yield off, o, end, compute
        off = end


def fingerprint(key):
    return struct.unpack_from('<II', key, DRIVER_ID)


def stamp(key, machine):
    """Key with this machine's driver id/version/feature bits. machine = (driver_id, driver_version, feature_bits)."""
    k = bytearray(key)
    raw1 = struct.unpack_from('<I', k, STATE)[0]
    struct.pack_into('<I', k, STATE, (raw1 & ~FEATURE_MASK) | (machine[2] & FEATURE_MASK))
    struct.pack_into('<II', k, DRIVER_ID, machine[0], machine[1])
    return bytes(k)


def machine_from_key(key):
    """(driver_id, driver_version, feature_bits) as written by an emulator run on this machine."""
    d, v = struct.unpack_from('<II', key, DRIVER_ID)
    return d, v, struct.unpack_from('<I', key, STATE)[0] & FEATURE_MASK


# --- detecting the machine stamp from the system's Vulkan driver (no cache needed) ---------------
# Values of VkDriverId (vulkan_core.h), by the names vulkaninfo prints.
DRIVER_IDS = {name: i for i, name in enumerate([
    None, 'AMD_PROPRIETARY', 'AMD_OPEN_SOURCE', 'MESA_RADV', 'NVIDIA_PROPRIETARY',
    'INTEL_PROPRIETARY_WINDOWS', 'INTEL_OPEN_SOURCE_MESA', 'IMAGINATION_PROPRIETARY',
    'QUALCOMM_PROPRIETARY', 'ARM_PROPRIETARY', 'GOOGLE_SWIFTSHADER', 'GGP_PROPRIETARY',
    'BROADCOM_PROPRIETARY', 'MESA_LLVMPIPE', 'MOLTENVK', 'COREAVI_PROPRIETARY', 'JUICE_PROPRIETARY',
    'VERISILICON_PROPRIETARY', 'MESA_TURNIP', 'MESA_V3DV', 'MESA_PANVK', 'SAMSUNG_PROPRIETARY',
    'MESA_VENUS', 'MESA_DOZEN', 'MESA_NVK', 'IMAGINATION_OPEN_SOURCE_MESA', 'MESA_HONEYKRISP',
    'VULKAN_SC_EMULATION_ON_VULKAN'])
    if name}
# feature bit positions in FixedPipelineState::raw1 (fixed_pipeline_state.h)
BIT_EDS, BIT_EDS2, BIT_EDS2_LOGIC_OP, BIT_EDS3_BLEND, BIT_EDS3_ENABLES, BIT_VIDS, BIT_CWE = 0, 1, 2, 3, 4, 5, 20


def run_vulkaninfo(cmd=None, timeout=300):
    """Full `vulkaninfo` text. Tries vulkaninfo, then the Windows one reachable from WSL."""
    import shutil, subprocess
    cands = [cmd] if cmd else ['vulkaninfo', '/mnt/c/Windows/System32/vulkaninfo.exe']
    for c in cands:
        if c and (shutil.which(c) or os.path.exists(c)):
            try:
                r = subprocess.run([c], capture_output=True, text=True, timeout=timeout, errors='replace')
            except subprocess.TimeoutExpired:
                raise CacheError(f'{c} did not finish in {timeout}s; run it yourself, save the output and pass it with --vulkaninfo-file')
            if r.stdout:
                return r.stdout
    raise CacheError('vulkaninfo not found; install it or pass its saved output with --vulkaninfo-file')


def machine_from_vulkaninfo(text, gpu=0, dyna_state=2, vids=True):
    """Compute the machine stamp the emulator would write for GPU number `gpu`.

    Rules mirror Lichii (vk_pipeline_cache.cpp, vulkan_device.cpp): the extended dynamic state levels
    gate bits 0-4 (dyna_state 0..3, default 2 on desktop), bit 5 needs VK_EXT_vertex_input_dynamic_state and
    the vertex_input_dynamic_state setting (default on), bit 20 needs VK_EXT_color_write_enable.
    Returns ((driver_id, driver_version, feature_bits), description)."""
    import re
    parts = re.split(r'^GPU(\d+):\s*$', text, flags=re.M)
    sections = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    if gpu not in sections:
        raise CacheError(f'GPU{gpu} not in vulkaninfo output (found {sorted(sections)})')
    t = sections[gpu]

    def field(name):
        m = re.search(r'^\s*' + name + r'\s*=\s*(.+?)\s*$', t, flags=re.M)
        return m.group(1) if m else None

    def flag(name):
        return field(name) == 'true'

    def ext(name):
        return re.search(r'^\s*' + name + r'\s*:\s*extension revision', t, flags=re.M) is not None

    dname = field('driverID')
    if not dname or not dname.startswith('DRIVER_ID_') or dname[10:] not in DRIVER_IDS:
        raise CacheError(f'unknown driverID {dname!r}')
    m = re.search(r'driverVersion\s*=\s*\S+\s*\((\d+)\)', t)
    if not m:
        raise CacheError('driverVersion not found')
    driver_id, driver_version = DRIVER_IDS[dname[10:]], int(m.group(1))

    eds = dyna_state >= 1 and ext('VK_EXT_extended_dynamic_state') and flag('extendedDynamicState')
    eds2 = dyna_state >= 2 and ext('VK_EXT_extended_dynamic_state2') and flag('extendedDynamicState2')
    logic = eds2 and flag('extendedDynamicState2LogicOp')
    e3 = dyna_state >= 3 and ext('VK_EXT_extended_dynamic_state3')
    blend = e3 and flag('extendedDynamicState3ColorBlendEnable') and \
        flag('extendedDynamicState3ColorBlendEquation') and flag('extendedDynamicState3ColorWriteMask')
    enables = e3 and any(flag(n) for n in (
        'extendedDynamicState3DepthClampEnable', 'extendedDynamicState3LogicOpEnable',
        'extendedDynamicState3AlphaToCoverageEnable'))
    vi = vids and ext('VK_EXT_vertex_input_dynamic_state') and flag('vertexInputDynamicState')
    cwe = ext('VK_EXT_color_write_enable') and flag('colorWriteEnable')
    bits = 0
    for bit, on in ((BIT_EDS, eds), (BIT_EDS2, eds2), (BIT_EDS2_LOGIC_OP, logic), (BIT_EDS3_BLEND, blend),
                    (BIT_EDS3_ENABLES, enables), (BIT_VIDS, vi), (BIT_CWE, cwe)):
        bits |= int(bool(on)) << bit
    name = field('deviceName') or '?'
    return (driver_id, driver_version, bits), f'{name}, {dname[10:]}'


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')            # Windows consoles cannot print every character
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cache', nargs='?', help='cache file to convert records from (with no stamp option below, it must also contain '
                    'records your own emulator wrote, which supply this machine\'s stamp)')
    ap.add_argument('--source', action='append', help='extra cache file(s) to convert records from (default: same file)')
    ap.add_argument('--out', help='output file (must not exist and must not be an input)')
    ap.add_argument('--version', type=int, default=54, help='cache version (default 54)')
    ap.add_argument('--selftest', action='store_true',
                    help='only check that converting other machines\' records reproduces your own records exactly')
    g = ap.add_argument_group('machine stamp without a cache of your own')
    g.add_argument('--detect-system', action='store_true', help='read the driver from `vulkaninfo` (no emulator cache needed)')
    g.add_argument('--vulkaninfo-file', help='saved output of vulkaninfo to use instead of running it')
    g.add_argument('--vulkaninfo-cmd', help='vulkaninfo executable to run')
    g.add_argument('--gpu', type=int, default=0, help='GPU number in vulkaninfo (the emulator\'s vulkan_device setting, default 0)')
    g.add_argument('--dyna-state', type=int, choices=range(4), default=2,
                   help='the emulator\'s Extended Dynamic State setting 0-3 (Lichii desktop default 2)')
    g.add_argument('--no-vids', action='store_true', help='the emulator\'s vertex input dynamic state setting is off')
    g.add_argument('--driver-id', type=int, help='set the stamp by hand: VkDriverId')
    g.add_argument('--driver-version', type=lambda x: int(x, 0), help='set the stamp by hand: packed driverVersion')
    g.add_argument('--features', type=lambda x: int(x, 0), help='set the stamp by hand: feature bits of raw1')
    g.add_argument('--print-stamp', action='store_true', help='only print the stamp that would be used')
    a = ap.parse_args()

    # 0. machine stamp from the system or by hand (None: take it from the cache)
    system = None
    if a.driver_id is not None or a.driver_version is not None or a.features is not None:
        if None in (a.driver_id, a.driver_version, a.features):
            sys.exit('--driver-id, --driver-version and --features must be given together')
        system = (a.driver_id, a.driver_version, a.features & FEATURE_MASK), 'given by hand'
    elif a.detect_system or a.vulkaninfo_file:
        text = open(a.vulkaninfo_file, encoding='utf-8', errors='replace').read() if a.vulkaninfo_file \
            else run_vulkaninfo(a.vulkaninfo_cmd)
        system = machine_from_vulkaninfo(text, a.gpu, a.dyna_state, not a.no_vids)
    if system:
        print(f'system stamp: driver_id={system[0][0]} driver_version={system[0][1]:#x} features={system[0][2]:#x} ({system[1]})')
        if a.print_stamp:
            return
    elif a.print_stamp:
        sys.exit('--print-stamp needs --detect-system, --vulkaninfo-file or the manual stamp options')
    if not a.cache or (not a.out and not a.selftest):
        ap.error('a cache file and --out are required')

    paths = [a.cache] + (a.source or [])
    if not a.selftest:
        if os.path.exists(a.out):
            sys.exit(f'refusing to overwrite {a.out}')
        if os.path.realpath(a.out) in {os.path.realpath(p) for p in paths}:
            sys.exit('output must not be an input')
    maps = []
    for p in paths:
        f = open(p, 'rb')                       # read-only
        maps.append(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ))

    # 1. this machine's stamp: from the system, or the last graphics record of the main cache
    m0 = maps[0]
    if system:
        machine = system[0]
    else:
        last = None
        for off, eend, end, compute in read_records(m0, a.version):
            if not compute:
                last = bytes(m0[eend:end])
        if last is None:
            sys.exit('no graphics record to take the machine stamp from; use --detect-system')
        machine = machine_from_key(last)
    mine = machine[:2]
    print(f'this machine: driver_id={mine[0]} driver_version={mine[1]:#x} features={machine[2]:#x}')

    def mask(key, compute):
        if compute:
            return key
        k = bytearray(key)
        struct.pack_into('<I', k, STATE, struct.unpack_from('<I', k, STATE)[0] & ~FEATURE_MASK)
        k[DRIVER_ID:DRIVER_ID + 8] = bytes(8)
        return bytes(k)

    # 2. what this machine already has (graphics by its own stamp, compute: records after its first graphics record)
    have, theirs = set(), {}
    for idx, m in enumerate(maps):
        seen_mine = False
        for off, eend, end, compute in read_records(m, a.version):
            key = bytes(m[eend:end])
            if compute:
                mk = (True, key)
                is_mine = idx == 0 and seen_mine
            else:
                mk = (False, mask(key, False))
                is_mine = fingerprint(key) == mine
                seen_mine = seen_mine or is_mine
            if is_mine:
                have.add(mk)
            else:
                theirs[mk] = (idx, off, eend, end, compute)   # later records win (newer drivers come later)

    todo = [(v, mk) for mk, v in theirs.items() if mk not in have]
    todo.sort(key=lambda t: (t[0][0], t[0][1]))
    print(f'already in your cache: {len(have)}; other machines\' pipelines you lack: {len(todo)}')

    if a.selftest:
        ok = bad = 0
        for idx, m in enumerate(maps[:1]):
            for off, eend, end, compute in read_records(m, a.version):
                key = bytes(m[eend:end])
                if compute or fingerprint(key) != mine:
                    continue
                v = theirs.get((False, mask(key, False)))
                if v is None:
                    continue
                other = bytes(maps[v[0]][v[2]:v[3]])
                if stamp(other, machine) == key:
                    ok += 1
                else:
                    bad += 1
        print(f'selftest: converted records equal your own in {ok} of {ok + bad} shared pipelines')
        return

    # Records written with other dynamic-state features store other state words (dynamic state is zeroed),
    # so only the logic-op bit is known to be harmless to differ (verified NVIDIA 0x100027 -> AMD 0x100023).
    risky = collections.Counter()
    for (idx, off, eend, end, compute), mk in todo:
        if not compute:
            diff = (struct.unpack_from('<I', maps[idx], eend + STATE)[0] ^ machine[2]) & FEATURE_MASK & ~(1 << BIT_EDS2_LOGIC_OP)
            if diff:
                risky[diff] += 1
    if risky:
        print(f'WARNING: {sum(risky.values())} records come from machines whose dynamic-state features differ from '
              f'yours ({", ".join(f"{d:#x}: {n}" for d, n in risky.most_common(3))}). Their state words may not match '
              f'what your emulator writes, so the emulator may ignore them. Check with --selftest if you have your own records.')

    written = 0
    with open(a.out, 'xb') as out:
        out.write(MAGIC + struct.pack('<I', a.version))
        for (idx, off, eend, end, compute), mk in todo:
            m = maps[idx]
            key = bytes(m[eend:end])
            out.write(bytes(m[off:eend]))
            out.write(key if compute else stamp(key, machine))
            written += 1
    print(f'wrote {written} records to {a.out} ({os.path.getsize(a.out) / 1e6:.0f} MB)')


if __name__ == '__main__':
    try:
        main()
    except CacheError as e:
        sys.exit(f'error: {e}')
