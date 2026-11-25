# SPDX-License-Identifier: AGPL-3.0-or-later
cmake_minimum_required(VERSION 3.21)
project(taos-qt VERSION 0.1.0 LANGUAGES CXX)

# Qt 6.8 (Debian) is the floor; the code is written against that API and must
# keep building on 6.12. Deprecated Qt API is an error from 6.8 on.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
# The shell's shared theme/Style code (native/shell/common) is built before the
# strict Qt flags below and reused by the QML entry point.
find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui)
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/../shell/common/CMakeLists.txt)
  set(TAOS_COMMON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../shell/common)
  add_subdirectory(${TAOS_COMMON_DIR} taos_common)
  set_target_properties(taos_common PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()
# The window engine core (sway IPC, window model, minimise / restore / full size) backs the title bar.
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/../shell/window/core/CMakeLists.txt AND NOT TARGET taos_window_core)
  add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../shell/window/core taos_window_core)
endif()
add_compile_definitions(QT_DISABLE_DEPRECATED_BEFORE=0x060800 QT_NO_CAST_FROM_ASCII
                        QT_NO_CAST_TO_ASCII QT_NO_URL_CAST_FROM_STRING)

find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Test)
# Qt Quick is optional so the library and its tests build on a host without
# the QML development files; the demo and the QML registration need it.
find_package(Qt6 6.8 QUIET COMPONENTS Qml Quick QuickControls2)

enable_testing()
list(APPEND CMAKE_MODULE_PATH ${CMAKE_CURRENT_SOURCE_DIR}/cmake)
set(TAOS_APP_TEMPLATE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/app)
include(TaosNativeApp)

# ---- the Rust side: libtaos_client_ffi.a ---------------------------------
set(TAOS_NATIVE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(TAOS_CARGO_TARGET_DIR ${CMAKE_CURRENT_BINARY_DIR}/cargo-target CACHE PATH "cargo target dir (share one between build trees to avoid rebuilding the Rust side)")
set(TAOS_FFI_LIB ${TAOS_CARGO_TARGET_DIR}/release/libtaos_client_ffi.a)
option(TAOS_CARGO_OFFLINE "Pass --offline to cargo (use the vendored/cached crates)" OFF)
set(TAOS_CARGO "cargo" CACHE STRING "cargo executable (rustup shim is fine)")
set(_offline_flag "")
if(TAOS_CARGO_OFFLINE)
  set(_offline_flag --offline)
endif()
# Cargo decides what is stale; this target always runs it (a no-op is fast).
file(GLOB_RECURSE _ffi_sources CONFIGURE_DEPENDS
     ${TAOS_NATIVE_DIR}/taos-client/src/*.rs ${TAOS_NATIVE_DIR}/taos-client-ffi/src/*.rs)
add_custom_command(
  OUTPUT ${TAOS_FFI_LIB}
  COMMAND ${CMAKE_COMMAND} -E env CARGO_TARGET_DIR=${TAOS_CARGO_TARGET_DIR}
          ${TAOS_CARGO} build --release -p taos-client-ffi ${_offline_flag} -j 2
  WORKING_DIRECTORY ${TAOS_NATIVE_DIR}
  DEPENDS ${_ffi_sources} ${TAOS_NATIVE_DIR}/taos-client-ffi/Cargo.toml
          ${TAOS_NATIVE_DIR}/taos-client/Cargo.toml ${TAOS_NATIVE_DIR}/Cargo.lock
  COMMENT "cargo build taos-client-ffi (release)"
  VERBATIM)
add_custom_target(taos_client_ffi_build DEPENDS ${TAOS_FFI_LIB})
add_library(taos_client_ffi STATIC IMPORTED GLOBAL)
set_target_properties(taos_client_ffi PROPERTIES
  IMPORTED_LOCATION ${TAOS_FFI_LIB}
  INTERFACE_INCLUDE_DIRECTORIES ${TAOS_NATIVE_DIR}/taos-client-ffi/include)
add_dependencies(taos_client_ffi taos_client_ffi_build)
find_package(Threads REQUIRED)
set_property(TARGET taos_client_ffi PROPERTY INTERFACE_LINK_LIBRARIES
  Threads::Threads ${CMAKE_DL_LIBS} m)

add_subdirectory(taosqt)
add_subdirectory(tools)
add_subdirectory(tests)
if(Qt6Quick_FOUND AND Qt6QuickControls2_FOUND)
  add_subdirectory(demo)
else()
  message(STATUS "Qt Quick / QuickControls2 not found: taos-native-demo is not built")
endif()
