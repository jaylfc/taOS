# SPDX-License-Identifier: AGPL-3.0-or-later
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2 Test)
find_program(PYTHON3 python3 REQUIRED)

set(STORE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(FAKE_STORE_PY ${CMAKE_CURRENT_SOURCE_DIR}/fake_store_controller.py)
set(FAKE_BROWSER_SH ${STORE_DIR}/../../shell/webapp/tests/fakebrowser.sh)

function(taos_store_test name)
  add_executable(${name} ${name}.cpp)
  target_link_libraries(${name} PRIVATE taosqt Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::Qml taos_store_core)
  target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR}/tests ${STORE_DIR}/src)
  target_compile_definitions(${name} PRIVATE FAKE_APPS_PY="${FAKE_STORE_PY}" FAKE_BROWSER="${FAKE_BROWSER_SH}" FAKE_APPGROUPS="${CMAKE_CURRENT_SOURCE_DIR}/fake_appgroups.sh"
                             WEBAPPS_JSON="${STORE_DIR}/catalog/webapps.json")
  taos_apps_use_common(${name})
  set(_files)
  foreach(f ${TAOS_STORE_QML})
    list(APPEND _files ${STORE_DIR}/qml/${f})
  endforeach()
  qt_add_resources(${name} "${name}_store" PREFIX "/my.taos.store" BASE ${STORE_DIR}/qml FILES ${_files})
  qt_add_resources(${name} "${name}_catalog" PREFIX "/my.taos.store/catalog" BASE ${STORE_DIR}/catalog FILES ${STORE_DIR}/catalog/webapps.json)
  # a private runtime dir and home: the owner's session, themes and web apps are never touched
  add_test(NAME apps_${name} COMMAND ${CMAKE_COMMAND} -E env XDG_RUNTIME_DIR=${CMAKE_CURRENT_BINARY_DIR}/rt_${name}
           HOME=${CMAKE_CURRENT_BINARY_DIR}/home_${name} XDG_DATA_HOME=${CMAKE_CURRENT_BINARY_DIR}/home_${name}/data
           XDG_CONFIG_HOME=${CMAKE_CURRENT_BINARY_DIR}/home_${name}/config
           TAOS_APPEARANCE_MIRROR_DIR=${CMAKE_CURRENT_BINARY_DIR}/home_${name}/mirror
           TAOS_SYSTEM_APPEARANCE=${CMAKE_CURRENT_BINARY_DIR}/home_${name}/etc-appearance.json
           TAOS_SYSTEM_THEME_DIR=${CMAKE_CURRENT_BINARY_DIR}/home_${name}/system-themes
           TAOS_WEBAPP_NO_NETWORK=1 QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:${name}>)
  set_tests_properties(apps_${name} PROPERTIES TIMEOUT 300)
endfunction()

taos_store_test(tst_store_logic)
taos_store_test(tst_store_ui)
