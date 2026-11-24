# SPDX-License-Identifier: AGPL-3.0-or-later
cmake_minimum_required(VERSION 3.21)
project(taos-native-activity VERSION 0.1.0 LANGUAGES CXX)

# Standalone build of the Activity app on the frozen foundation (native/qt).
#   cmake -S native/apps/activity -B build -G Ninja -DCMAKE_PREFIX_PATH=<qt> -DTAOS_CARGO_OFFLINE=ON
# The foundation is pulled in with EXCLUDE_FROM_ALL so only what the app needs
# (taosqt and the Rust client) is built.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

set(TAOS_QT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../qt)
add_subdirectory(${TAOS_QT_DIR} foundation EXCLUDE_FROM_ALL)
set(TAOS_APP_TEMPLATE_DIR ${TAOS_QT_DIR}/app)
list(APPEND CMAKE_MODULE_PATH ${TAOS_QT_DIR}/cmake)
include(TaosNativeApp)

find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Qml Quick QuickControls2 Test)
enable_testing()
add_compile_definitions(QT_DISABLE_DEPRECATED_BEFORE=0x060800 QT_NO_CAST_FROM_ASCII
                        QT_NO_CAST_TO_ASCII QT_NO_URL_CAST_FROM_STRING)

add_subdirectory(src)
add_subdirectory(tests)
