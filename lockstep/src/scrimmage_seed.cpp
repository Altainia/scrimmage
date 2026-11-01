// libscrimmage_seed: makes std::random_device give the same numbers in every run, so code that seeds its random
// generators from it (REVLib's C++ simulated sensor noise, in robotpy-rev) repeats exactly under scrimmage.
//
// scrimmage loads it into a RobotPy robot program run with --scrimmage-lockstep through LD_PRELOAD, so this
// definition of libstdc++'s std::random_device::_M_getval wins over libstdc++'s own. Only libstdc++ (what
// RobotPy's Linux wheels use) has this symbol; for anything else the library has no effect.

#include <cstdint>

namespace {
uint32_t state = 0x9e3779b9u;  // xorshift32; any fixed nonzero start works
}

// unsigned int std::random_device::_M_getval()
extern "C" __attribute__((visibility("default"))) unsigned int _ZNSt13random_device9_M_getvalEv(void*) {
  state ^= state << 13;
  state ^= state >> 17;
  state ^= state << 5;
  return state;
}
