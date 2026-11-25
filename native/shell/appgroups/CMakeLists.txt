# SPDX-License-Identifier: AGPL-3.0-or-later
# The container apps engine: taos_appgroups_core (Qt Core only) and the CLI taos-appgroups.
# Standalone:   cmake -S native/shell/appgroups -B build -G Ninja -DCMAKE_PREFIX_PATH=<qt>
# From another project (the Store links the core): add_subdirectory(.../shell/appgroups appgroups).
cmake_minimum_required(VERSION 3.21)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(taos-appgroups VERSION 0.1.0 LANGUAGES CXX)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_AUTOMOC ON)
  find_package(Qt6 6.8 REQUIRED COMPONENTS Core Test)
  enable_testing()
else()
  find_package(Qt6 6.8 REQUIRED COMPONENTS Core Test)
endif()

file(GLOB TAOS_APPGROUPS_CATALOG CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/catalog/*.json)
file(GLOB TAOS_APPGROUPS_ICONS CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/icons/*.svg)

add_library(taos_appgroups_core STATIC src/appgroups.cpp src/appgroups.h src/manifest.cpp src/manifest.h src/owner.cpp src/owner.h src/callbackbroker.cpp src/callbackbroker.h)
target_include_directories(taos_appgroups_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(taos_appgroups_core PUBLIC Qt6::Core)
set_target_properties(taos_appgroups_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# The bundled catalogue and the generic icons travel inside the binary (the object library keeps them linked
# into a static library consumer).
qt_add_resources(taos_appgroups_core "taos_appgroups_data" PREFIX "/appgroups/catalog" BASE ${CMAKE_CURRENT_SOURCE_DIR}/catalog FILES ${TAOS_APPGROUPS_CATALOG})
qt_add_resources(taos_appgroups_core "taos_appgroups_icons" PREFIX "/appgroups/icons" BASE ${CMAKE_CURRENT_SOURCE_DIR}/icons FILES ${TAOS_APPGROUPS_ICONS})

add_executable(taos-appgroups src/main.cpp)
target_link_libraries(taos-appgroups PRIVATE taos_appgroups_core)
install(TARGETS taos-appgroups RUNTIME DESTINATION bin)

add_executable(tst_appgroups tests/tst_appgroups.cpp)
target_link_libraries(tst_appgroups PRIVATE taos_appgroups_core Qt6::Test)
target_compile_definitions(tst_appgroups PRIVATE FAKE_INCUS="${CMAKE_CURRENT_SOURCE_DIR}/tests/fakeincus.sh"
                           APPGROUPS_CLI="$<TARGET_FILE:taos-appgroups>" CATALOG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/catalog")
add_dependencies(tst_appgroups taos-appgroups)
add_test(NAME appgroups_core COMMAND ${CMAKE_COMMAND} -E env QT_QPA_PLATFORM=offscreen $<TARGET_FILE:tst_appgroups>)
set_tests_properties(appgroups_core PROPERTIES TIMEOUT 120)
