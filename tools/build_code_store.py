#!/usr/bin/env python3
"""Regenerate the lowered shader code of a game from its own files, for `hints.py import`.

Steps (each prints what it does; a finished step is skipped when you run the script again):
  1. read the shader-bearing files out of the .nup (no keys needed)       -> <work>/romfs
  2. extract the native programs with Lichii's tools/nx2/extract_static_shaders.py -> <work>/extracted
  3. build the small C++ programs in harness/ against Lichii's own sources (g++)   -> <work>/bin
  4. lower every program with Lichii's LowerProgram and write the code store:
       <work>/store/gfx/<hash>.bin   graphics shaders
       <work>/store/mesh/<hash>.bin  task and mesh shaders, plus index.tsv
       <work>/store/compute.bin      generated compute records (cache format)

Needs: Python 3.14 (or a Python with `backports.zstd`, passed with --extractor-python). Either ready-made
hash_dump/compute_hash programs in <bin-dir> (the Windows bundle ships them), or g++ (C++20) and a clone of
Lichii (--lichii) to build them. Nothing is written outside <work>.
The shader code it produces comes from your game and must not be shared.
"""
import argparse, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = '.exe' if os.name == 'nt' else ''
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

# files holding shaders: Shader/*, Layout2Shader/*, effect bundles, the engine library archives, zstd dictionaries
PATTERN = re.compile(r'^romfs/(Shader/|Layout2Shader/|Pack/ZsDic\.pack\.zs$|Effect/.*\.vsetb\.byml\.zs$|'
                     r'Lib/(agl|Terrain|gsys)/[^/]*_resource\.[^/]*\.sarc\.zs$)')


def step(msg):
    print(f'\n== {msg}', flush=True)


def read_nup(nup, romfs):
    from nup_read import Nup
    n = Nup(nup)
    chosen = {}
    for pk in sorted(range(len(n.packages)), key=lambda i: n.packages[i]['kind']):   # base first, update overlays
        for path, fi in n.walk(n.packages[pk]['root']):
            if PATTERN.match(path):
                chosen[path] = fi
    total = 0
    for path, fi in chosen.items():
        dest = os.path.join(romfs, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        data = n.read(fi)
        with open(dest, 'xb') as f:
            f.write(data)
        total += len(data)
    print(f'{len(chosen)} files, {total / 1e6:.0f} MB')


def extract(py, lichii, romfs, out, title):
    sources = []
    for base, _, files in os.walk(romfs):
        for name in sorted(files):
            p = os.path.join(base, name)
            if not p.endswith('ZsDic.pack.zs'):
                sources += ['--source', f'{title.upper()}={p}']
    args = ['--read-only-root', romfs, '--output', out] + sources
    dictionary = os.path.join(romfs, 'romfs', 'Pack', 'ZsDic.pack.zs')
    if os.path.exists(dictionary):                           # not every game ships a zstd dictionary pack
        args += ['--zstd-dictionary-pack', dictionary]
    nx2 = os.path.join(lichii, 'tools', 'nx2')
    # The extractor exits nonzero when some containers are unsupported (e.g. NDSR packages); the manifest is still written.
    if os.path.normcase(py) == os.path.normcase(sys.executable):
        # Same Python: run it in-process (Windows limits a command line to 32,767 characters, a game has >1,400 files).
        sys.path.insert(0, nx2)
        import extract_static_shaders
        old_argv, sys.argv = sys.argv, ['extract_static_shaders.py'] + args
        try:
            extract_static_shaders.main()
        except SystemExit:
            pass
        finally:
            sys.argv = old_argv
    else:
        subprocess.run([py, os.path.join(nx2, 'extract_static_shaders.py')] + args, cwd=nx2)
    manifest = os.path.join(out, 'manifest.json')
    if not os.path.exists(manifest):
        sys.exit('extraction failed: no manifest was written')
    print(f"{len(json.load(open(manifest))['errors'])} containers could not be read (listed in the manifest)")


def build(lichii, binary_dir, prebuilt=None, lowering_src=None):
    """lowering_src: the src folder of another emulator of the family (for example Fritchii) whose lowering code
    is used instead of Lichii's. Its headers come first; Lichii still supplies warm_records.cpp."""
    os.makedirs(binary_dir, exist_ok=True)
    if prebuilt:                                           # ready-made programs (the Windows bundle): just copy them
        for tool in ('hash_dump', 'compute_hash'):
            shutil.copy(os.path.join(prebuilt, tool + EXE), os.path.join(binary_dir, tool + EXE))
        return
    src = os.path.join(lichii, 'src')
    common = ['g++', '-std=c++20', '-O2', '-I' + os.path.join(ROOT, 'harness', 'stub')] + \
             (['-I' + lowering_src] if lowering_src else []) + ['-I' + src]
    libs = [os.path.join(src, 'video_core', 'shader_warm', 'warm_records.cpp'),
            os.path.join(lowering_src or src, 'common', 'cityhash.cpp')]
    lowering = os.path.join(src, 'video_core', 'native_lowering.cpp')   # newer Lichii: LowerNativeProgram moved out of warm_records.cpp
    if os.path.exists(lowering) and lowering_src:
        sys.exit('--lowering-src needs a Lichii checkout whose warm_records.cpp does the lowering itself (build-125)')
    if os.path.exists(lowering):
        libs.append(lowering)
    for tool in ('hash_dump', 'compute_hash'):
        subprocess.run(common + [os.path.join(ROOT, 'harness', tool + '.cpp')] + libs + ['-o', os.path.join(binary_dir, tool + EXE)], check=True)


def lower(work):
    manifest = json.load(open(os.path.join(work, 'extracted', 'manifest.json')))
    base = os.path.join(work, 'extracted')
    store = os.path.join(work, 'store')
    for d in ('gfx', 'mesh'):
        os.makedirs(os.path.join(store, d))
    lists = {'gfx': [], 'mesh': [], 'compute': []}
    for p in manifest['programs']:
        code = os.path.join(base, p['code'])
        if p['stage'] == 'compute':
            lists['compute'].append(f"{code}\t{os.path.join(base, p['control'])}")
        elif p.get('format') == 'native-header':
            lists['mesh' if p['stage'] in ('task', 'mesh') else 'gfx'].append(f"{code}\t{p['stage']}")
    for name, lines in lists.items():
        with open(os.path.join(work, f'{name}.tsv'), 'w') as f:
            f.write('\n'.join(lines) + '\n')
    binary = os.path.join(work, 'bin')
    for name in ('gfx', 'mesh'):
        env = dict(os.environ, DUMP_DIR=os.path.join(store, name))
        out = subprocess.run([os.path.join(binary, 'hash_dump' + EXE), os.path.join(work, f'{name}.tsv')], env=env,
                             capture_output=True, text=True, encoding='utf-8', errors='replace', check=True).stdout.splitlines()
        ok = [l.split('\t') for l in out if l.startswith('OK')]
        print(f'{name}: {len(ok)} of {len(lines := lists[name])} programs lowered')
        if name == 'mesh':                                       # task/mesh stage of each hash
            with open(os.path.join(store, 'mesh', 'index.tsv'), 'w') as f:
                for r in ok:
                    f.write(f'{r[2]}\t{r[1]}\n')
    out = subprocess.run([os.path.join(binary, 'compute_hash' + EXE), os.path.join(work, 'compute.tsv'),
                          os.path.join(store, 'compute.bin')], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True).stdout.splitlines()
    print(f'compute: {sum(1 for l in out if l.startswith("OK"))} of {len(lists["compute"])} programs lowered')


def build_store(nup, work, lichii, bin_dir, extractor_python, title, lowering_src=None):
    """Run the four steps; returns the code store folder (<work>/store)."""
    prebuilt = bin_dir if all(os.path.exists(os.path.join(bin_dir, t + EXE)) for t in ('hash_dump', 'compute_hash')) else None
    if lowering_src:
        prebuilt = None                                    # ready-made programs hold Lichii's lowering
        if not os.path.exists(os.path.join(lowering_src, 'shader_recompiler', 'frontend', 'ampere', 'lower_to_maxwell.h')):
            sys.exit(f'{lowering_src} is not the src folder of an emulator with the native lowering code')
    if not prebuilt and not shutil.which('g++'):
        sys.exit('g++ (C++20) is required when no ready-made programs are in ' + bin_dir)
    if not os.path.exists(os.path.join(lichii, 'tools', 'nx2', 'extract_static_shaders.py')):
        sys.exit(f'extract_static_shaders.py not found under {lichii}/tools/nx2')
    os.makedirs(work)
    romfs, extracted = os.path.join(work, 'romfs'), os.path.join(work, 'extracted')
    step('1/4 reading shader files from the .nup'); read_nup(nup, romfs)
    step('2/4 extracting native programs'); extract(extractor_python, lichii, romfs, extracted, title)
    step('3/4 building the lowering programs'); build(lichii, os.path.join(work, 'bin'), prebuilt, lowering_src)
    step('4/4 lowering'); lower(work)
    return os.path.join(work, 'store')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')            # Windows consoles cannot print every character
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--nup', required=True, help='the game .nup (base and update in one file)')
    ap.add_argument('--title', help='title id, 16 hex digits (default: read from the .nup)')
    ap.add_argument('--lichii', default=os.path.join(ROOT, 'lichii'),
                    help='a clone of the Lichii repository, or the lichii folder of the Windows bundle (default)')
    ap.add_argument('--bin-dir', default=os.path.join(ROOT, 'bin'),
                    help='folder with ready-made hash_dump and compute_hash programs; used when present, else g++ builds them')
    ap.add_argument('--extractor-python', default=sys.executable,
                    help='python that runs extract_static_shaders.py (3.14 or with backports.zstd)')
    ap.add_argument('--lowering-src', help='src folder of another emulator (Fritchii) whose shader lowering to use; '
                    '--lichii must then be build-125')
    ap.add_argument('--work', required=True, help='new working folder (must not exist)')
    a = ap.parse_args()
    a.nup, a.lichii, a.work, a.bin_dir = (os.path.abspath(x) for x in (a.nup, a.lichii, a.work, a.bin_dir))
    a.extractor_python = os.path.abspath(shutil.which(a.extractor_python) or a.extractor_python)
    if os.path.exists(a.work):
        sys.exit(f'{a.work} exists; use a new folder')
    if not a.title:
        from nup_read import Nup
        a.title = f'{Nup(a.nup).packages[0]["title"]:016x}'
        print('title id from the .nup:', a.title)
    store = build_store(a.nup, a.work, a.lichii, a.bin_dir, a.extractor_python, a.title,
                        os.path.abspath(a.lowering_src) if a.lowering_src else None)
    print(f'\ncode store ready: {store}\n'
          f'  hints.py import <hints> --out <cache> --gfx-dir {os.path.join(store, "gfx")} '
          f'--mesh-dir {os.path.join(store, "mesh")} --compute-file {os.path.join(store, "compute.bin")} --detect-system')

if __name__ == '__main__':
    main()
