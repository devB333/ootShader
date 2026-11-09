#pragma once
#include <string>
namespace fmt { template <class... A> std::string format(const char*, A&&...) { return {}; } }
