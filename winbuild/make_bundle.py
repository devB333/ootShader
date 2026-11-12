#!/usr/bin/env python3
"""Assemble the portable Windows bundle (a folder and a .zip): everything an end user needs, nothing to install.

  python3 winbuild/make_bundle.py --python-embed python-3.14.x-embed-amd64.zip --lichii <Lichii build-125 checkout> \\
          --bin <folder with hash_dump.exe and compute_hash.exe> [--out winbuild/scb-oot-win64]

Inputs: the python.org Windows embeddable package (3.14, for compression.zstd), a Lichii checkout (three files of
tools/nx2 and the GPL text are copied, GPL-3.0), the two helper programs built from harness/ for Windows, and this
repository's tools/, harness/ and hints/. .github/workflows/windows-bundle.yml runs it.
"""
import argparse, os, shutil, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = ('convert_cache.py', 'hints.py', 'build_code_store.py', 'nup_read.py', 'cityhash.py', 'scb_oot.py',
         'diagnose_missing.py', 'README.md')
NX2 = ('extract_static_shaders.py', 'shader_archive.py', 'shader_containers.py')
HARNESS = ('hash_dump.cpp', 'compute_hash.cpp', 'key_hash.cpp', 'stub/fmt/format.h', 'stub/common/logging.h')
FILES = ('README.txt', 'NOTICE.txt', 'scb-oot.bat', 'scb-oot-fritchii.bat')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--python-embed', required=True, help='python-3.14.x-embed-amd64.zip from python.org')
    ap.add_argument('--lichii', required=True, help='Lichii checkout the helper programs were built against (build-125)')
    ap.add_argument('--bin', required=True, help='folder with hash_dump.exe and compute_hash.exe')
    ap.add_argument('--out', default=os.path.join(ROOT, 'winbuild', 'scb-oot-win64'))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if os.path.exists(out):
        sys.exit(f'{out} exists; remove it first')

    def put(src, dst):
        os.makedirs(os.path.dirname(os.path.join(out, dst)), exist_ok=True)
        shutil.copy(src, os.path.join(out, dst))

    # Python: the whole embeddable package. Its ._pth file lists where modules come from (it ignores PYTHONPATH).
    with zipfile.ZipFile(a.python_embed) as z:
        z.extractall(os.path.join(out, 'python'))
    pth = [n for n in os.listdir(os.path.join(out, 'python')) if n.endswith('._pth')]
    if len(pth) != 1 or not os.path.exists(os.path.join(out, 'python', 'python.exe')):
        sys.exit(f'{a.python_embed} is not a Windows embeddable Python package')
    with open(os.path.join(out, 'python', pth[0]), 'w') as f:
        f.write(pth[0].replace('._pth', '.zip') + '\n.\n..\\tools\n..\\lichii\\tools\\nx2\n')

    for n in TOOLS:
        put(os.path.join(ROOT, 'tools', n), f'tools/{n}')
    for n in NX2:
        put(os.path.join(a.lichii, 'tools', 'nx2', n), f'lichii/tools/nx2/{n}')
    for n in ('hash_dump', 'compute_hash'):
        put(os.path.join(a.bin, n + '.exe'), f'bin/{n}.exe')
    for n in HARNESS:
        put(os.path.join(ROOT, 'harness', n), f'source/harness/{n}')
    put(os.path.join(ROOT, 'hints', 'combined.hints'), 'hints/combined.hints')
    put(os.path.join(ROOT, 'LICENSE'), 'LICENSE-GPL-3.0.txt')
    for n in FILES:
        put(os.path.join(ROOT, 'winbuild', 'files', n), n)
    zpath = shutil.make_archive(out, 'zip', os.path.dirname(out), os.path.basename(out))
    print(out, '\n', zpath, os.path.getsize(zpath) // 1000000, 'MB')


if __name__ == '__main__':
    main()
