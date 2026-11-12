"""python3 -I tests/test_hints.py  (synthetic data, no game or cache needed)"""
import os, struct, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import hints as H


class T(unittest.TestCase):
    def test_roundtrip_and_normalisation(self):
        state = bytearray(H.STATE_SIZE)
        struct.pack_into('<I', state, 0, 0x100023 | 0x40)       # features + a real bit
        struct.pack_into('<II', state, 16, 4, 0x1234)            # driver id/version of the writer
        state2 = bytearray(state); struct.pack_into('<I', state2, 64, 7)
        a = ('g', ((0, 11, 0, 0, 0, 22), H.normalize_state(bytes(state))))
        b = ('g', ((0, 12, 0, 0, 0, 22), H.normalize_state(bytes(state2))))
        c = ('c', bytes(range(24)))
        recs = {a: [b'A' * 80, b'B' * 90], b: [b'A' * 80, b'C' * 90], c: [b'D' * 100]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.hints')
            H.dump(recs, '0400486024dec000', 54, p, '', {a: 0, b: 3})
            head, back, ages = H.load(p)
        self.assertEqual(head['records'], 3)
        self.assertEqual(back, recs)
        self.assertEqual(ages, {a: 0, b: 3})
        n = H.normalize_state(bytes(state))
        self.assertEqual(n[16:24], bytes(8))                      # no machine stamp inside hints
        self.assertEqual(struct.unpack_from('<I', n, 0)[0], 0x40)

    def test_key_hash_sizes(self):
        key = bytes(48) + struct.pack('<I', 0x100023) + bytes(1004)     # dynamic vertex input only
        self.assertEqual(H.hashed_state_size(0x100023), 112)
        self.assertEqual(H.hashed_state_size(0x100023 | 8), 80)
        self.assertEqual(H.hashed_state_size(1), 368)
        self.assertEqual(H.hashed_state_size(0), 432)
        self.assertEqual(H.hashed_state_size(0x40), 1008)
        self.assertEqual(H.city_hash64(b''), H.city_hash64(b''))        # imported and callable
        self.assertEqual(H.key_hash(key), H.city_hash64(key[:48 + 112]))

    def test_cache_version_50(self):
        def meta(stage, cbuf_keys, tail):
            body = b''.join(struct.pack('<QI', k, 7) for k in cbuf_keys)
            return struct.pack('<6Q7I', 16, 0, 0, len(cbuf_keys), 0, 0, 0, 0, 0, 0, 8, 1, stage) + body + tail
        with_entry = meta(4, [5, H.READ_PROPERTIES_KEY], b'S' * 80)
        self.assertEqual(H.legacy_meta(with_entry), meta(4, [5], b'S' * 80))
        self.assertEqual(H.legacy_meta(meta(0, [5], b'S' * 80)), meta(0, [5], b'S' * 80))
        state = H.normalize_state(bytes(H.STATE_SIZE))
        recs = {('g', ((0, 11, 0, 0, 0, 22), state)): [meta(8, [], b'S' * 172), meta(4, [], b'S' * 80)]}
        store = H.CodeStore(None, None, None)
        self.assertEqual([r[1] for r in H.rebuild(recs, store, (1, 2, 3), 50)], [H.NOT_STORED])
        with self.assertRaises(H.HintError):
            list(H.rebuild(recs, store, (1, 2, 3), 51))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'bad')
            open(p, 'wb').write(b'not hints at all')
            with self.assertRaises(H.HintError):
                H.load(p)


if __name__ == '__main__':
    unittest.main()
