#!/usr/bin/env python3
"""Read-only reader for Lichii/Eden NUP containers (magic "NUP\\x01"): list files, extract some.
Layout from core/file_sys/nup_container.cpp. No keys are involved; members are raw or zstd.
  nup_read.py list  <file.nup> [--package N] [--grep TEXT]
  nup_read.py get   <file.nup> <path-in-package> <out-file> [--package N]
"""
import argparse, ctypes, ctypes.util, mmap, struct, sys

class Nup:
    def __init__(self, path):
        self.f = open(path, 'rb'); self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        m = self.m
        if m[:4] != b'NUP\x01': raise SystemExit('not a NUP file')
        toc_off, toc_size = struct.unpack_from('<QQ', m, 8)
        self.blob_offset, self.file_size = struct.unpack_from('<QQ', m, 0x40)
        toc = m[toc_off:toc_off + toc_size]
        magic, npk, ndir, nfile, pool_off, pool_size = struct.unpack_from('<IIQQQQ', toc, 0)
        if magic != 0x01434F54: raise SystemExit('bad TOC')
        p = 0x30
        self.packages = []
        for i in range(npk):
            title, root, exefs, romfs, ver, kind = struct.unpack_from('<QIIIIB', toc, p + i * 0x40)
            self.packages.append(dict(title=title, root=root, exefs=exefs, romfs=romfs, version=ver, kind=kind))
        p += npk * 0x40
        self.dirs = [struct.unpack_from('<IIII', toc, p + i * 0x18) for i in range(ndir)]; p += ndir * 0x18
        self.files = []
        for i in range(nfile):
            r = toc[p + i * 0x50:p + (i + 1) * 0x50]
            no, nl, size, eoff, esize = struct.unpack_from('<IIQQQ', r, 0)
            self.files.append((no, nl, size, eoff, esize, r[0x40]))
        p += nfile * 0x50
        self.children = struct.unpack_from(f'<{(pool_off - p) // 4}I', toc, p)
        self.pool = toc[pool_off:pool_off + pool_size]
        self.ndir = ndir
    def name(self, off, ln): return self.pool[off:off + ln].decode('utf-8', 'replace')
    def walk(self, d, prefix=''):
        no, nl, first, count = self.dirs[d]
        for c in self.children[first:first + count]:
            if c < self.ndir:
                dn = self.dirs[c]; yield from self.walk(c, prefix + self.name(dn[0], dn[1]) + '/')
            else:
                fi = self.files[c - self.ndir]; yield prefix + self.name(fi[0], fi[1]), fi
    def read(self, fi):
        no, nl, size, eoff, esize, codec = fi
        raw = self.m[eoff:eoff + esize]
        if codec == 0: return bytes(raw)
        z = _zstd_module()
        if z is not None:                                    # Python 3.14 (compression.zstd) or backports.zstd
            out = z.decompress(bytes(raw))
            if len(out) != size: raise SystemExit('zstd failed')
            return out
        lib = ctypes.CDLL(ctypes.util.find_library('zstd'))    # fall back to a system libzstd
        lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        lib.ZSTD_decompress.restype = ctypes.c_size_t
        out = ctypes.create_string_buffer(size)
        if lib.ZSTD_decompress(out, size, bytes(raw), len(raw)) != size: raise SystemExit('zstd failed')
        return out.raw


def _zstd_module():
    try:
        from compression import zstd
        return zstd
    except ImportError:
        try:
            from backports import zstd
            return zstd
        except ImportError:
            return None


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['list', 'get']); ap.add_argument('nup')
    ap.add_argument('rest', nargs='*'); ap.add_argument('--package', type=int, default=0); ap.add_argument('--grep')
    a = ap.parse_args(); n = Nup(a.nup); pk = n.packages[a.package]
    if a.cmd == 'list':
        print('packages:', [(hex(p['title']), p['version'], p['kind']) for p in n.packages])
        tot = 0
        for path, fi in n.walk(pk['root']):
            if a.grep and a.grep.lower() not in path.lower(): continue
            tot += fi[2]; print(f'{fi[2]:>12} {"z" if fi[5] else "r"} {path}')
        print('total uncompressed bytes listed:', tot)
    else:
        want = a.rest[0]
        for path, fi in n.walk(pk['root']):
            if path == want or path.endswith('/' + want):
                open(a.rest[1], 'xb').write(n.read(fi)); print('wrote', path, fi[2]); break
        else: raise SystemExit('not found')
