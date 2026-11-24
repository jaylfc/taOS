# SPDX-License-Identifier: AGPL-3.0-or-later
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2 Test)
find_program(PYTHON3 python3 REQUIRED)

set(AGENTS_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(FAKE_AGENTS_PY ${CMAKE_CURRENT_SOURCE_DIR}/fake_agents_controller.py)

function(taos_agents_test name)
  add_executable(${name} ${name}.cpp)
  target_link_libraries(${name} PRIVATE taosqt Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::Qml taos_agents_engine)
  target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR}/tests ${AGENTS_DIR}/src)
  target_compile_definitions(${name} PRIVATE FAKE_APPS_PY="${FAKE_AGENTS_PY}")
  taos_apps_use_common(${name})
  set(_files)
  foreach(f ${TAOS_AGENTS_QML})
    list(APPEND _files ${AGENTS_DIR}/${f})
  endforeach()
  qt_add_resources(${name} "${name}_agents" PREFIX "/my.taos.agents" BASE ${AGENTS_DIR} FILES ${_files})
  add_test(NAME apps_${name} COMMAND ${CMAKE_COMMAND} -E env XDG_RUNTIME_DIR=${CMAKE_CURRENT_BINARY_DIR}/rt_${name}
           QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:${name}>)
  set_tests_properties(apps_${name} PROPERTIES TIMEOUT 300)
endfunction()

foreach(t tst_agents_logic tst_agents_ui)
  taos_agents_test(${t})
endforeach()
