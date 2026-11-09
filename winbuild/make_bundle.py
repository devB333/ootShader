#!/usr/bin/env python3
"""Assemble the portable Windows bundle winbuild/scb-oot-win64 (and a .zip next to it).

Inputs (already in the project): winbuild/dl/py/python-embed.zip (python.org embeddable, checked against its published
SHA-256), winbuild/bin/*.exe (cross-compiled with zig), tools/, harness/, out/hints/*.hints, and three files of Lichii's
tools/nx2 (GPL-3.0) in src/."""
import os, shutil, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'winbuild', 'scb-oot-win64')
if os.path.exists(OUT):
    sys.exit(f'{OUT} exists; remove it first')


def put(src, dst):
    os.makedirs(os.path.dirname(os.path.join(OUT, dst)), exist_ok=True)
    shutil.copy(os.path.join(ROOT, src), os.path.join(OUT, dst))


# Python: keep only what the tools import (list measured on a full run, see files/stdlib-needed.txt)
KEEP_FILES = {'python.exe', 'python314.dll', 'python3.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', '_ctypes.pyd',
              'libffi-8.dll', '_lzma.pyd', '_zstd.pyd', '_bz2.pyd', 'LICENSE.txt', 'python314._pth'}
NEEDED = {l.strip() for l in open(os.path.join(ROOT, 'winbuild', 'files', 'stdlib-needed.txt')) if l.strip()}
src = zipfile.ZipFile(os.path.join(ROOT, 'winbuild', 'dl', 'py', 'python-embed.zip'))
os.makedirs(os.path.join(OUT, 'python'))
for name in src.namelist():
    if name in KEEP_FILES:
        open(os.path.join(OUT, 'python', name), 'wb').write(src.read(name))
inner = zipfile.ZipFile(__import__('io').BytesIO(src.read('python314.zip')))
with zipfile.ZipFile(os.path.join(OUT, 'python', 'python314.zip'), 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for info in inner.infolist():
        mod = info.filename.rsplit('.', 1)[0].replace('/', '.')
        mod = mod[:-9] if mod.endswith('.__init__') else mod
        top = mod.split('.')[0]
        if mod in NEEDED or top == 'encodings':
            z.writestr(info.filename, inner.read(info.filename))
with open(os.path.join(OUT, 'python', 'python314._pth'), 'w') as f:       # the embedded python ignores PYTHONPATH
    f.write('python314.zip\n.\n..\\tools\n..\\lichii\\tools\\nx2\n')
for n in ('convert_cache.py', 'hints.py', 'build_code_store.py', 'nup_read.py', 'cityhash.py', 'scb_oot.py', 'README.md'):
    put(f'tools/{n}', f'tools/{n}')
for n in ('extract_static_shaders.py', 'shader_archive.py', 'shader_containers.py'):
    put(f'src/tools/nx2/{n}', f'lichii/tools/nx2/{n}')
for n in ('hash_dump', 'compute_hash'):
    put(f'winbuild/bin_small/{n}.exe', f'bin/{n}.exe')
for n in ('hash_dump.cpp', 'compute_hash.cpp'):
    put(f'harness/{n}', f'source/harness/{n}')
put('harness/stub/fmt/format.h', 'source/harness/stub/fmt/format.h')
put('out/hints/combined.hints', 'hints/combined.hints')        # nvidia.hints and amd.hints go in a separate small zip
put('src/LICENSES/GPL-3.0-or-later.txt', 'LICENSE-GPL-3.0.txt')
for n in ('README.txt', 'NOTICE.txt', 'scb-oot.bat'):
    put(f'winbuild/files/{n}', n)
with zipfile.ZipFile(OUT + '-extra-hints.zip', 'w', zipfile.ZIP_STORED) as z:
    for n in ('nvidia.hints', 'amd.hints'):
        z.write(os.path.join(ROOT, 'out', 'hints', n), 'hints/' + n)
zpath = shutil.make_archive(OUT, 'zip', os.path.dirname(OUT), os.path.basename(OUT))
print(OUT, '\n', zpath, os.path.getsize(zpath) // 1000000, 'MB')
