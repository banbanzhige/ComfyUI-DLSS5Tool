// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <algorithm>
#include <atomic>
#include <array>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <optional>
#include <vector>
#include <string>
#include <windows.h>

namespace Realtime {
struct ParameterMode { uint32_t effect; std::string name; int mode; };
inline std::mutex parameterMutex;
inline std::vector<ParameterMode> parameterModes;
inline std::vector<ParameterMode> ReadParameterModes() {
    std::lock_guard lock(parameterMutex); return parameterModes;
}
inline std::atomic<int> gpuPriority = 2; // D3DKMT NORMAL; never steal realtime priority from the game.
struct Metrics {
    uint64_t captured = 0, processed = 0, submitted = 0, real = 0, generated = 0;
    bool generatedKnown = true;
    double ageP50 = 0, ageP95 = 0, ageP99 = 0;
    double processP95 = 0;
    uint32_t ageSamples = 0, processSamples = 0;
    double latestAge = 0;
    uint64_t validAges = 0;
    uint64_t nrEvaluations = 0, nrFailures = 0, gpuSamples = 0,
        qualityGpuSamples = 0, droppedCapture = 0;
    double nrGpuP95 = 0;
    double qualityGpuP95 = 0;
};
inline std::mutex metricsMutex;
inline Metrics counters;
inline std::array<double, 512> ages{}, durations{};
inline std::array<double, 512> gpuDurations{};
inline std::array<double, 512> qualityGpuDurations{};
inline std::optional<uint64_t> lastRealId;
inline std::array<std::pair<uint64_t, double>, 128> dequeued{};
inline double Qpc100ns() {
    LARGE_INTEGER now{}, frequency{};
    QueryPerformanceCounter(&now); QueryPerformanceFrequency(&frequency);
    return double(now.QuadPart) * 10000000.0 / double(frequency.QuadPart);
}
inline void ResetMetrics() {
    std::lock_guard lock(metricsMutex); counters = {}; lastRealId.reset(); dequeued = {};
}
inline void Captured(uint64_t frameId) {
    std::lock_guard lock(metricsMutex); ++counters.captured;
    dequeued[frameId % dequeued.size()] = {frameId, Qpc100ns()};
}
inline void CaptureDrained(uint32_t count) { std::lock_guard lock(metricsMutex); counters.droppedCapture += count > 0 ? count-1 : 0; }
inline void NrEvaluated(bool ok) { std::lock_guard lock(metricsMutex); if(ok) ++counters.nrEvaluations; else ++counters.nrFailures; }
inline void NrGpu(double ms) { std::lock_guard lock(metricsMutex); gpuDurations[counters.gpuSamples++ % gpuDurations.size()] = ms; }
inline void QualityGpu(double ms) {
    std::lock_guard lock(metricsMutex);
    qualityGpuDurations[counters.qualityGpuSamples++ % qualityGpuDurations.size()] = ms;
}
inline void Processed(double milliseconds) {
    std::lock_guard lock(metricsMutex);
    durations[counters.processed++ % durations.size()] = milliseconds;
    counters.processSamples = uint32_t(std::min<uint64_t>(counters.processed, durations.size()));
}
inline void Submitted(uint64_t id, int64_t timestamp, bool generated, std::optional<uint32_t> count) {
    std::lock_guard lock(metricsMutex);
    ++counters.submitted;
    bool real = !generated && id != lastRealId && (!count || *count > 0);
    if (real) { ++counters.real; lastRealId = id; }
    if (!count) counters.generatedKnown = false;
    else counters.generated += generated ? *count : (*count > 1 ? *count - 1 : 0);
    const double now = Qpc100ns();
    // WGC may report a compositor timestamp ahead of QPC at submission. Do
    // not silently clamp it or call it latency. Measure from our actual dequeue.
    counters.latestAge = timestamp > 0 ? (now - timestamp) / 10000.0 : 0;
    const auto& input = dequeued[id % dequeued.size()];
    const double age = (now - input.second) / 10000.0;
    if (real && input.first == id && age >= 0 && age < 60000) {
        ages[counters.validAges++ % ages.size()] = age;
        counters.ageSamples = uint32_t(std::min<uint64_t>(counters.validAges, ages.size()));
    }
}
inline Metrics ReadMetrics() {
    std::lock_guard lock(metricsMutex);
    auto result = counters;
    auto percentile = [](const auto& values, uint32_t count, double fraction) {
        if (!count) return 0.0;
        std::vector<double> sorted(values.begin(), values.begin() + count);
        std::sort(sorted.begin(), sorted.end());
        return sorted[std::min(size_t(count-1), size_t(fraction * (count-1)))];
    };
    result.ageP50 = percentile(ages, result.ageSamples, .50);
    result.ageP95 = percentile(ages, result.ageSamples, .95);
    result.ageP99 = percentile(ages, result.ageSamples, .99);
    result.processP95 = percentile(durations, result.processSamples, .95);
    result.nrGpuP95 = percentile(gpuDurations, uint32_t(std::min<uint64_t>(result.gpuSamples, gpuDurations.size())), .95);
    result.qualityGpuP95 = percentile(qualityGpuDurations,
        uint32_t(std::min<uint64_t>(result.qualityGpuSamples, qualityGpuDurations.size())), .95);
    return result;
}
}
