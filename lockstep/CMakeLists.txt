cmake_minimum_required(VERSION 3.21)
# No language yet: off Linux nothing is compiled, so no C++ compiler is needed there.
project(scrimmage_lockstep LANGUAGES NONE)

# On Linux `pip install` builds and installs the two libraries. Elsewhere pyproject.toml skips CMake altogether and the
# package is pure Python. On Windows it carries a prebuilt extension (scrimmage_lockstep/lib/win_amd64), which CI
# builds by running CMake here directly (.github/workflows/ci.yml, the windows job); lockstep/tools/stamp.py records
# which source it came from.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" OR WIN32)
  enable_language(CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_CXX_VISIBILITY_PRESET hidden)

  # The HAL headers and libwpiHal.so (wpiHal.lib on Windows) come from the robotpy-native-wpihal wheel (a build
  # requirement on Linux; a dependency of robotpy-hal, installed with RobotPy, for Windows CI's build).
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
  if(WIN32)
    # wpiHal.lib is the import library of wpiHal.dll: the DLL records wpiHal.dll by name, so at runtime it binds to
    # the HAL the robot process already loaded. No seed library: REV's noise isn't pinned on Windows (docs/rev.md).
    target_link_libraries(halsim_scrimmage_lockstep PRIVATE "${WPIHAL_DIR}/lib/wpiHal.lib")
    install(TARGETS halsim_scrimmage_lockstep RUNTIME DESTINATION scrimmage_lockstep/lib/win_amd64)
  else()
    # libwpiHal.so's SONAME is "libwpiHal.so", so this records NEEDED libwpiHal.so (no path): at runtime
    # it binds to the HAL the robot process already loaded.
    target_link_libraries(halsim_scrimmage_lockstep PRIVATE "${WPIHAL_DIR}/lib/libwpiHal.so")

    # Fixed std::random_device numbers, preloaded into RobotPy robots by scrimmage (see src/scrimmage_seed.cpp).
    add_library(scrimmage_seed SHARED src/scrimmage_seed.cpp)

    install(TARGETS halsim_scrimmage_lockstep scrimmage_seed LIBRARY DESTINATION scrimmage_lockstep/lib)
  endif()
else()
  message(STATUS "lockstep is built on Linux and Windows only: installing scrimmage_lockstep as pure Python")
endif()
