cmake_minimum_required(VERSION 3.21)
project(taos-shell VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# The Qt prefix and the LayerShellQt prefix come from CMAKE_PREFIX_PATH
# (see dev-build.sh); nothing machine specific lives in this tree.
find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Qml Quick Test Network DBus)
find_package(LayerShellQt REQUIRED)

enable_testing()

set(TAOS_COMMON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/common)
add_subdirectory(common)
add_subdirectory(display)
add_subdirectory(bar)
add_subdirectory(greeter)
add_subdirectory(window)
add_subdirectory(overview)
add_subdirectory(dock)
add_subdirectory(frame)
add_subdirectory(launcher)

# Shell session entry point test (needs only a POSIX sh).
add_test(NAME session-script
    COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/test-session.sh
            ${CMAKE_CURRENT_SOURCE_DIR}/taos-shell-session)
set_tests_properties(session-script PROPERTIES TIMEOUT 120)

install(PROGRAMS taos-shell-session DESTINATION bin)
