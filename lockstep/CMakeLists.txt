cmake_minimum_required(VERSION 3.21)
project(scrimmage_lockstep LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)

# The HAL headers and libwpiHal.so come from the robotpy-native-wpihal wheel (a build requirement).
find_package(Python COMPONENTS Interpreter REQUIRED)
execute_process(
  COMMAND "${Python_EXECUTABLE}" -c
          "import native.wpihal; print(list(native.wpihal.__path__)[0])"
  OUTPUT_VARIABLE WPIHAL_DIR
  OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
# HAL headers include <wpi/string.h>, from robotpy-native-wpiutil (a dependency of robotpy-native-wpihal).
execute_process(
  COMMAND "${Python_EXECUTABLE}" -c
          "import native.wpiutil; print(list(native.wpiutil.__path__)[0])"
  OUTPUT_VARIABLE WPIUTIL_DIR
  OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)

add_library(halsim_scrimmage_lockstep SHARED src/scrimmage_lockstep.cpp)
target_include_directories(halsim_scrimmage_lockstep PRIVATE "${WPIHAL_DIR}/include" "${WPIUTIL_DIR}/include")
# libwpiHal.so's SONAME is "libwpiHal.so", so this records NEEDED libwpiHal.so (no path): at runtime
# it binds to the HAL the robot process already loaded.
target_link_libraries(halsim_scrimmage_lockstep PRIVATE "${WPIHAL_DIR}/lib/libwpiHal.so")

# Fixed std::random_device numbers, preloaded into RobotPy robots by scrimmage (see src/scrimmage_seed.cpp).
add_library(scrimmage_seed SHARED src/scrimmage_seed.cpp)

install(TARGETS halsim_scrimmage_lockstep scrimmage_seed LIBRARY DESTINATION scrimmage_lockstep/lib)
