# SPDX-License-Identifier: AGPL-3.0-or-later
# Native taOS first-party apps built on the Qt foundation (native/qt).
# This is a top-level project: it pulls the foundation in as a subdirectory and
# leaves native/qt untouched.
#   cmake -S native/apps -B <build> -G Ninja -DCMAKE_PREFIX_PATH=<qt> ...
cmake_minimum_required(VERSION 3.21)
project(taos-native-apps VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Test Qml Quick QuickControls2)
find_package(Qt6 6.8 QUIET COMPONENTS QuickShapes)
enable_testing()

# The foundation. Its directory-scoped settings (module path, app template dir)
# do not reach this directory, so they are repeated here.
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../qt taosqt-foundation)
set(TAOS_QT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../qt)
list(APPEND CMAKE_MODULE_PATH ${TAOS_QT_DIR}/cmake)
set(TAOS_APP_TEMPLATE_DIR ${TAOS_QT_DIR}/app)
include(TaosNativeApp)

add_compile_definitions(QT_DISABLE_DEPRECATED_BEFORE=0x060800 QT_NO_CAST_FROM_ASCII
                        QT_NO_CAST_TO_ASCII QT_NO_URL_CAST_FROM_STRING)

add_subdirectory(common)
add_subdirectory(notifications)
add_subdirectory(decisions)
add_subdirectory(agents)
add_subdirectory(messages)
add_subdirectory(store)
find_package(Qt6 6.8 REQUIRED COMPONENTS Concurrent)
add_subdirectory(survival)
add_subdirectory(tests)
