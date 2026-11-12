#include <cstdio>
#include <optional>
#include <span>
#include <string>
#include <cstdlib>
#include <cstring>
#include <span>
#include <fstream>
#include <iterator>
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
int main(int, char** argv) {
    std::ifstream list(argv[1]); std::string line;
    while (std::getline(list, line)) {
        auto t = line.find('\t'); std::string path = line.substr(0, t);
        std::ifstream f(path, std::ios::binary);
        std::vector<u8> all{std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()}; std::vector<u8> code(all.begin() + (all.size() > 128 ? 128 : 0), all.end());
        LoweredCode low; auto err = Lower(code, low);
        if (!err && all.size() > 128 && low.code.size() >= 80) std::memcpy(low.code.data(), all.data(), 80);
        if (err) std::printf("ERR\t%s\t%s\n", line.substr(t + 1).c_str(), err->c_str());
        else { if (const char* dir = std::getenv("DUMP_DIR")) { char nm[512]; std::snprintf(nm, sizeof nm, "%s/%016llx.bin", dir, (unsigned long long)StageHash(low.code)); if (FILE* o = std::fopen(nm, "wb")) { std::fwrite(low.code.data(), 1, low.code.size(), o); std::fclose(o);} } std::printf("OK\t%s\t%016llx\t%zu\t%016llx\n", line.substr(t + 1).c_str(), (unsigned long long)StageHash(low.code), low.code.size(), (unsigned long long)StageHash(std::span<const u8>(low.code).subspan(80))); }
    }
}
