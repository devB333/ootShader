// Lowers and hashes the extracted compute programs with Lichii's own code (read-only reference in ../src).
#include <cstdio>
#include <optional>
#include <span>
#include <string>
#include <fstream>
#include <iterator>
#include <sstream>
#include "video_core/shader_warm/warm_records.h"
using namespace VideoCommon::ShaderWarm;
// LowerProgram(program, out) in older sources, LowerProgram(program, graphics, out) in newer ones. The harness
// strips and restores the graphics header itself, so it always asks for the headerless (compute) form.
template <class Out>
static std::optional<std::string> Lower(std::span<const u8> program, Out& out) {
    if constexpr (requires { LowerProgram(program, out); }) {
        return LowerProgram(program, out);
    } else {
        return LowerProgram(program, false, out);
    }
}
static std::vector<u8> slurp(const std::string& p) {
    std::ifstream f(p, std::ios::binary);
    return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}
int main(int argc, char** argv) {
    std::ifstream list(argv[1]); std::string line;
    FILE* out = argc > 2 ? std::fopen(argv[2], "wb") : nullptr;
    if (out) { std::fwrite("yuzucach", 1, 8, out); u32 v = 54; std::fwrite(&v, 4, 1, out); }
    while (std::getline(list, line)) {
        auto t = line.find('\t');
        auto code = slurp(line.substr(0, t)), ctl = slurp(line.substr(t + 1));
        LoweredCode lowered; ComputeKey key{};
        auto err = Lower(code, lowered);
        auto c = ReadComputeControl(ctl);
        if (err || !c) { std::printf("ERR\t%s\t%s\n", line.substr(0, t).c_str(), err ? err->c_str() : "control"); continue; }
        auto rec = ComputeEntry(lowered, *c, key);
        if (out) std::fwrite(rec.data(), 1, rec.size(), out);
        std::printf("OK\t%s\t%016llx\t%u\t%u\t%u\t%u\n", line.substr(0, t).c_str(), (unsigned long long)StageHash(lowered.code),
                    c->shared_memory, c->workgroup[0], c->workgroup[1], c->workgroup[2]);
    }
}
