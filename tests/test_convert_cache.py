"""python3 -I tests/test_convert_cache.py  (no game or cache needed)"""
import os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import convert_cache as cc

SAMPLE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vkinfo_sample.txt')).read()


class T(unittest.TestCase):
    def test_detect_defaults(self):
        m, _ = cc.machine_from_vulkaninfo(SAMPLE)
        self.assertEqual(m, (1, 0x80014B, 0x100023))

    def test_detect_levels(self):
        self.assertEqual(cc.machine_from_vulkaninfo(SAMPLE, dyna_state=3)[0][2], 0x10003B)
        self.assertEqual(cc.machine_from_vulkaninfo(SAMPLE, dyna_state=1)[0][2], 0x100001 | 0x20)
        self.assertEqual(cc.machine_from_vulkaninfo(SAMPLE, dyna_state=0)[0][2], 0x100020)
        self.assertEqual(cc.machine_from_vulkaninfo(SAMPLE, vids=False)[0][2], 0x100003)

    def test_bad_input(self):
        with self.assertRaises(cc.CacheError):
            cc.machine_from_vulkaninfo(SAMPLE, gpu=3)
        with self.assertRaises(cc.CacheError):
            cc.machine_from_vulkaninfo(SAMPLE.replace('DRIVER_ID_AMD_PROPRIETARY', 'DRIVER_ID_NOPE'))

    def test_stamp_roundtrip(self):
        key = bytearray(cc.KEY_GRAPHICS)
        struct.pack_into('<I', key, cc.STATE, 0xABC00000 | 0x27)
        out = cc.stamp(bytes(key), (1, 0x80014B, 0x100023))
        self.assertEqual(cc.machine_from_key(out), (1, 0x80014B, 0x100023))
        self.assertEqual(struct.unpack_from('<I', out, cc.STATE)[0] & ~cc.FEATURE_MASK, 0xABC00000)
        self.assertEqual(out[:cc.STATE], bytes(key[:cc.STATE]))

    def test_rejects_other_files(self):
        with self.assertRaises(cc.CacheError):
            list(cc.read_records(b'notacache' * 10, 54))


if __name__ == '__main__':
    unittest.main()
