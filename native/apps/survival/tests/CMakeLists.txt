# SPDX-License-Identifier: AGPL-3.0-or-later
# Tests of Survival Studio. The engine test and the CLI run with no display; the window test loads the real QML
# offscreen in a private home, against the apps' fake controller.
find_package(Qt6 6.8 REQUIRED COMPONENTS Test Quick QuickControls2)
find_program(PYTHON3 python3)

add_executable(tst_survival_engine tst_survival_engine.cpp)
target_link_libraries(tst_survival_engine PRIVATE taos_survival_core Qt6::Test)
target_include_directories(tst_survival_engine PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(tst_survival_engine PRIVATE SURVIVAL_CLI="$<TARGET_FILE:taos-survival-cli>"
                           SURVIVAL_CATALOG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../catalog")
add_dependencies(tst_survival_engine taos-survival-cli)
taos_survival_rpath(tst_survival_engine)
add_test(NAME apps_tst_survival_engine COMMAND ${CMAKE_COMMAND} -E env QT_QPA_PLATFORM=offscreen
         TAOS_SURVIVAL_ALLOW_FILE_URLS=1 $<TARGET_FILE:tst_survival_engine>)
set_tests_properties(apps_tst_survival_engine PROPERTIES TIMEOUT 300)

add_executable(tst_survival_packs tst_survival_packs.cpp)
target_link_libraries(tst_survival_packs PRIVATE taos_survival_core Qt6::Test)
target_include_directories(tst_survival_packs PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(tst_survival_packs PRIVATE SURVIVAL_CATALOG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../catalog" SURVIVAL_TEST_DATA="${CMAKE_CURRENT_SOURCE_DIR}/data")
taos_survival_rpath(tst_survival_packs)
add_test(NAME apps_tst_survival_packs COMMAND ${CMAKE_COMMAND} -E env QT_QPA_PLATFORM=offscreen
         TAOS_SURVIVAL_ALLOW_FILE_URLS=1 $<TARGET_FILE:tst_survival_packs>)
set_tests_properties(apps_tst_survival_packs PROPERTIES TIMEOUT 300)

if(PYTHON3)
  add_executable(tst_survival_ui tst_survival_ui.cpp)
  target_link_libraries(tst_survival_ui PRIVATE taosqt Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::Qml taos_survival_faces)
  target_include_directories(tst_survival_ui PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/tests)
  target_compile_definitions(tst_survival_ui PRIVATE SURVIVAL_TEST_DATA="${CMAKE_CURRENT_SOURCE_DIR}/data" FAKE_APPS_PY="${CMAKE_SOURCE_DIR}/tests/fake_apps_controller.py")
  taos_apps_use_common(tst_survival_ui)
  set(_files)
  foreach(f ${TAOS_SURVIVAL_QML})
    list(APPEND _files ${CMAKE_CURRENT_SOURCE_DIR}/../qml/${f})
  endforeach()
  qt_add_resources(tst_survival_ui "tst_survival_ui_qml" PREFIX "/my.taos.survival" BASE ${CMAKE_CURRENT_SOURCE_DIR}/../qml FILES ${_files})
  taos_survival_catalog(tst_survival_ui)
  taos_survival_rpath(tst_survival_ui)
  # a private runtime dir and home: the owner's session, themes and data are never touched
  add_test(NAME apps_tst_survival_ui COMMAND ${CMAKE_COMMAND} -E env XDG_RUNTIME_DIR=${CMAKE_CURRENT_BINARY_DIR}/rt_ui
           HOME=${CMAKE_CURRENT_BINARY_DIR}/home_ui XDG_DATA_HOME=${CMAKE_CURRENT_BINARY_DIR}/home_ui/data
           XDG_CONFIG_HOME=${CMAKE_CURRENT_BINARY_DIR}/home_ui/config TMPDIR=${CMAKE_CURRENT_BINARY_DIR}
           TAOS_APPEARANCE_MIRROR_DIR=${CMAKE_CURRENT_BINARY_DIR}/home_ui/mirror
           TAOS_SYSTEM_APPEARANCE=${CMAKE_CURRENT_BINARY_DIR}/home_ui/etc-appearance.json
           TAOS_SYSTEM_THEME_DIR=${CMAKE_CURRENT_BINARY_DIR}/home_ui/system-themes
           TAOS_SURVIVAL_ALLOW_FILE_URLS=1 QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:tst_survival_ui>)
  set_tests_properties(apps_tst_survival_ui PROPERTIES TIMEOUT 300)
endif()
