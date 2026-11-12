// Stands in for Lichii's common/logging.h (which needs the fmt library): the lowering code only logs.
#pragma once
#define LOG_TRACE(...) (void(0))
#define LOG_DEBUG(...) (void(0))
#define LOG_INFO(...) (void(0))
#define LOG_WARNING(...) (void(0))
#define LOG_ERROR(...) (void(0))
#define LOG_CRITICAL(...) (void(0))
