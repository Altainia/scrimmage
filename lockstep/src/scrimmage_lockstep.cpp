// scrimmage_lockstep: a HALSim extension that lets scrimmage step the robot program's clock.
//
// It pauses WPILib timing as soon as it loads and creates a "ScrimmageLockstep" SimDevice, which
// halsim_ws_client relays to scrimmage over the existing WebSocket. scrimmage writes step_seq;
// a worker thread advances the clock by step_us with HALSIM_StepTiming (which runs due notifiers,
// including TimedRobot's loop, and waits for them) and then reports done_seq. Only the HAL C API
// is used, so one build works with RobotPy's and GradleRIO's HAL of the same WPILib year.
//
// halsim_ws_client starts relaying a SimDevice created mid-run from its own network thread, so a device the robot
// creates during a step can reach scrimmage after that step's done_seq. With each done_seq the extension therefore
// also reports sim_values, how many distinct SimDevice values exist, and scrimmage waits until it has heard of that
// many before it uses the step's outputs.
// Design: docs/superpowers/specs/2026-10-11-lockstep-design.md

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include <hal/Extensions.h>
#include <hal/Notifier.h>
#include <hal/SimDevice.h>
#include <hal/Value.h>
#include <hal/simulation/MockHooks.h>
#include <hal/simulation/NotifierData.h>
#include <hal/simulation/SimDeviceData.h>

namespace {

using Clock = std::chrono::steady_clock;
constexpr auto kWatchdog = std::chrono::seconds(2);
constexpr auto kPoll = std::chrono::milliseconds(50);
constexpr auto kStartCap = std::chrono::seconds(30);  // longest wait for the program to start once engaged
constexpr auto kShutdownWait = std::chrono::milliseconds(500);  // first wait for the worker at shutdown
constexpr auto kUnstickWait = std::chrono::seconds(1);          // wait after stopping the notifiers

// Every distinct (device, value) name the robot program's SimDevices have created. HAL runs these callbacks with
// its own lock released, on whichever thread created the device or value.
std::mutex gValuesMutex;
std::set<std::string> gValueNames;
std::atomic<int64_t> gValueCount{0};

void OnValueCreated(const char* name, void* param, HAL_SimValueHandle, int32_t, const HAL_Value*) {
  const auto* device = static_cast<const std::string*>(param);
  std::lock_guard lock{gValuesMutex};
  gValueNames.insert(*device + '\0' + name);
  gValueCount = static_cast<int64_t>(gValueNames.size());
}

void OnDeviceCreated(const char* name, void*, HAL_SimDeviceHandle handle) {
  auto* device = new std::string(name);  // lives as long as the callback (the whole process)
  HALSIM_RegisterSimValueCreatedCallback(handle, device, OnValueCreated, true);
}

struct State {
  HAL_SimValueHandle engage = 0, stepUs = 0, stepSeq = 0, engaged = 0, doneSeq = 0, keepalive = 0, simValues = 0;
  std::mutex m;
  std::condition_variable cv;
  bool engagePending = false;     // guarded by m
  bool disengagePending = false;  // guarded by m
  bool keepalivePending = false;  // guarded by m
  bool selfRelease = false;       // guarded by m: the next engage=false is our own release(), not scrimmage
  int64_t requestedSeq = 0;    // guarded by m
  bool stop = false;           // guarded by m
  bool exited = false;         // guarded by m: the worker thread has returned
  std::thread worker;
};

void Log(const char* msg) { std::fprintf(stderr, "scrimmage_lockstep: %s\n", msg); }

std::chrono::duration<double> GracePeriod() {
  const char* s = std::getenv("SCRIMMAGE_LOCKSTEP_GRACE_S");
  double v = 5.0;
  if (s != nullptr) {
    char* end = nullptr;
    double parsed = std::strtod(s, &end);
    if (end != s && parsed >= 0) v = parsed;
  }
  return std::chrono::duration<double>(v);
}

// Value callbacks run on whichever thread set the value (the WebSocket client's loop for values from
// scrimmage). They only record and wake the worker: stepping timing here would stall that loop.
void OnEngage(const char*, void* param, HAL_SimValueHandle, int32_t, const HAL_Value* value) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    if (!value->data.v_boolean && s->selfRelease) {
      s->selfRelease = false;  // release() writing engage=false; leave any request that raced in alone
    } else {
      s->engagePending = value->data.v_boolean;
      s->disengagePending = !value->data.v_boolean;
    }
  }
  s->cv.notify_all();
}

void OnKeepalive(const char*, void* param, HAL_SimValueHandle, int32_t, const HAL_Value*) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    s->keepalivePending = true;
  }
  s->cv.notify_all();
}

void OnStepSeq(const char*, void* param, HAL_SimValueHandle, int32_t, const HAL_Value* value) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    s->requestedSeq = value->data.v_long;
  }
  s->cv.notify_all();
}

void WorkerLoop(State* s) {
  enum class Mode { kPending, kEngaged, kFree };
  Mode mode = Mode::kPending;  // paused at load, waiting for scrimmage
  const auto grace = GracePeriod();
  const auto loaded = Clock::now();
  auto lastActivity = loaded;
  int64_t done = 0;
  int64_t seenSeq = 0;
  // Steps before the robot program has started (constructor, robotInit) would move its clock by an amount
  // that depends on wall-clock timing, so engaging waits for program start.
  bool started = false;
  bool waiting = false;  // scrimmage asked to engage, but the program hasn't started yet
  auto waitingSince = loaded;

  // Engage (pausing first if the clock is running free). Called without s->m held, because setting
  // a value fires that value's callbacks on this thread.
  auto engage = [&] {
    if (mode == Mode::kFree) HALSIM_PauseTiming();
    if (mode != Mode::kEngaged) {
      mode = Mode::kEngaged;
      HAL_SetSimValueBoolean(s->engaged, true);
      Log("engaged by scrimmage");
    }
    lastActivity = Clock::now();
  };
  auto release = [&](const char* why) {
    HALSIM_ResumeTiming();
    mode = Mode::kFree;
    HAL_SetSimValueBoolean(s->engaged, false);
    if (HAL_GetSimValueBoolean(s->engage)) {  // only a real change fires OnEngage
      {
        std::lock_guard l{s->m};
        s->selfRelease = true;
      }
      HAL_SetSimValueBoolean(s->engage, false);  // so scrimmage's next engage=true is a change
    }
    Log(why);
  };

  std::unique_lock lock{s->m};
  while (true) {
    s->cv.wait_for(lock, kPoll, [&] {
      return s->stop || s->engagePending || s->disengagePending || s->keepalivePending || (started && s->requestedSeq > done);
    });
    if (s->stop) return;
    const bool wantEngage = s->engagePending;
    const bool wantDisengage = s->disengagePending;
    s->engagePending = s->disengagePending = false;
    const bool keepalive = s->keepalivePending;
    s->keepalivePending = false;
    const int64_t seq = s->requestedSeq;
    lock.unlock();

    if (!started) started = HALSIM_GetProgramStarted();
    const bool newRequest = wantEngage || seq > seenSeq;
    if (seq > seenSeq) seenSeq = seq;
    auto now = Clock::now();
    if (keepalive && mode == Mode::kEngaged) lastActivity = now;  // scrimmage is holding us paused on purpose

    if (wantDisengage) {
      waiting = false;
      if (mode != Mode::kFree) release("disengaged by scrimmage; running in real time");
    }
    if (!started) {
      // Hold engage and step requests (paused) until the program starts.
      if (newRequest && !waiting) {
        waiting = true;
        waitingSince = now;
        if (mode == Mode::kFree) {
          HALSIM_PauseTiming();
          mode = Mode::kPending;
        }
      }
      if (waiting && now - waitingSince > kStartCap) {
        waiting = false;
        release("the robot program did not start within 30 s of scrimmage engaging; running in real time");
      } else if (!waiting && mode == Mode::kPending && now - loaded > grace) {
        release("scrimmage did not engage within the grace period; running in real time");
      }
    } else {
      if (newRequest || waiting) {
        waiting = false;
        engage();
      }
      if (seq > done) {
        engage();  // a step request always gets its step, even after the watchdog released the clock
        int64_t us = HAL_GetSimValueLong(s->stepUs);
        if (us > 0) HALSIM_StepTiming(static_cast<uint64_t>(us));
        HAL_SetSimValueLong(s->simValues, gValueCount.load());  // relayed before done_seq (same device, in order)
        HAL_SetSimValueLong(s->doneSeq, seq);
        done = seq;
        lastActivity = Clock::now();
      } else if (mode == Mode::kPending && now - loaded > grace) {
        release("scrimmage did not engage within the grace period; running in real time");
      } else if (mode == Mode::kEngaged && now - lastActivity > kWatchdog) {
        release("no step requests from scrimmage for 2 s; running in real time");
      }
    }
    lock.lock();
  }
}

void Worker(State* s) {
  WorkerLoop(s);
  {
    std::lock_guard lock{s->m};
    s->exited = true;
  }
  s->cv.notify_all();
}

bool WaitExited(State* s, std::chrono::milliseconds timeout) {
  std::unique_lock lock{s->m};
  return s->cv.wait_for(lock, timeout, [&] { return s->exited; });
}

// Stop every notifier, which wakes a HALSIM_StepTiming that is waiting for one of them to finish.
void StopNotifiers() {
  std::vector<HALSIM_NotifierInfo> infos(64);
  int32_t n = HALSIM_GetNotifierInfo(infos.data(), static_cast<int32_t>(infos.size()));
  if (n > static_cast<int32_t>(infos.size())) {
    infos.resize(n);
    n = HALSIM_GetNotifierInfo(infos.data(), static_cast<int32_t>(infos.size()));
  }
  if (n > static_cast<int32_t>(infos.size())) n = static_cast<int32_t>(infos.size());
  for (int32_t i = 0; i < n; ++i) {
    int32_t status = 0;
    HAL_StopNotifier(infos[i].handle, &status);
  }
}

void Shutdown(void* param) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    s->stop = true;
  }
  s->cv.notify_all();
  if (!s->worker.joinable()) return;
  // If the robot's main thread died mid-loop (an uncaught exception), the worker is stuck in
  // HALSIM_StepTiming waiting for notifiers that will never finish. Stopping them releases it.
  if (!WaitExited(s, kShutdownWait)) {
    Log("the robot program stopped mid-step; stopping its notifiers so it can exit");
    StopNotifiers();  // HAL calls: never with s->m held
    if (!WaitExited(s, kUnstickWait)) {
      // A deterministic exit beats tearing the process down under a thread still inside the HAL.
      Log("the step worker is still stuck at shutdown; exiting with code 1");
      std::fflush(nullptr);
      std::_Exit(1);
    }
  }
  s->worker.join();
}

}  // namespace

extern "C" {

__attribute__((visibility("default"))) int HALSIM_InitExtension(void) {
  HAL_SimDeviceHandle dev = HAL_CreateSimDevice("ScrimmageLockstep");
  if (dev == 0) {
    Log("could not create the ScrimmageLockstep SimDevice; lockstep disabled");
    return 0;
  }
  auto* s = new State;  // lives for the whole process
  s->engage = HAL_CreateSimValueBoolean(dev, "engage", HAL_SimValueInput, false);
  s->stepUs = HAL_CreateSimValueLong(dev, "step_us", HAL_SimValueInput, 5000);
  s->stepSeq = HAL_CreateSimValueLong(dev, "step_seq", HAL_SimValueInput, 0);
  s->engaged = HAL_CreateSimValueBoolean(dev, "engaged", HAL_SimValueOutput, false);
  s->doneSeq = HAL_CreateSimValueLong(dev, "done_seq", HAL_SimValueOutput, 0);
  s->keepalive = HAL_CreateSimValueLong(dev, "keepalive", HAL_SimValueInput, 0);
  s->simValues = HAL_CreateSimValueLong(dev, "sim_values", HAL_SimValueOutput, 0);
  HALSIM_RegisterSimDeviceCreatedCallback("", nullptr, OnDeviceCreated, true);  // counts its own values too

  HALSIM_PauseTiming();  // before the robot's first loop, so every run starts on the same step grid
  // RobotPy loads extensions after importing robot.py, so its clock has already run a little: restart it, so the
  // robot's FPGA time starts at the same time under every language. Restarting while paused makes it exactly 0
  // (restarting first would let a few wall-clock microseconds in before the pause, different every run); then step it
  // to 1 us: AdvantageKit's LoggedRobot sets its first loop's notifier alarm at 0 and takes a wake-up at FPGA time 0
  // for "notifier stopped", so at 0 it would quit at once. Steps are relative, so loops keep their step grid.
  HALSIM_RestartTiming();
  HALSIM_StepTimingAsync(1);
  HALSIM_RegisterSimValueChangedCallback(s->engage, s, OnEngage, false);
  HALSIM_RegisterSimValueChangedCallback(s->stepSeq, s, OnStepSeq, false);
  HALSIM_RegisterSimValueChangedCallback(s->keepalive, s, OnKeepalive, false);
  s->worker = std::thread(Worker, s);
  HAL_OnShutdown(s, Shutdown);
  Log("loaded; robot clock paused until scrimmage engages");
  return 0;
}

}  // extern "C"
