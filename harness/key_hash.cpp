// Pipeline key hashes as Lichii's GraphicsPipelineCacheKey::Hash() computes them: CityHash64 over the bytes.
// usage: key_hash <in> <out>; in = repeated [u32 length][bytes], out = one u64 per input.
#include <cstdio>
#include <cstdint>
#include <vector>
#include "common/cityhash.h"
int main(int, char** argv) {
    FILE* in = std::fopen(argv[1], "rb"); FILE* out = std::fopen(argv[2], "wb");
    if (!in || !out) return 1;
    uint32_t n;
    while (std::fread(&n, 4, 1, in) == 1) {
        std::vector<char> b(n);
        if (std::fread(b.data(), 1, n, in) != n) return 2;
        uint64_t h = Common::CityHash64(b.data(), n);
        std::fwrite(&h, 8, 1, out);
    }
    return 0;
}
