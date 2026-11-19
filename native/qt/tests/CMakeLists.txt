set(FAKE_PY ${CMAKE_CURRENT_SOURCE_DIR}/../../testing/fake_controller.py)
find_program(PYTHON3 python3 REQUIRED)

function(taos_qt_test name)
  add_executable(${name} ${name}.cpp)
  target_link_libraries(${name} PRIVATE taosqt Qt6::Test)
  target_compile_definitions(${name} PRIVATE FAKE_CONTROLLER_PY="${FAKE_PY}"
                             TAOS_TEST_SRC_DIR="${CMAKE_CURRENT_SOURCE_DIR}")
  add_test(NAME ${name} COMMAND ${name})
  set_tests_properties(${name} PROPERTIES TIMEOUT 120 ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
endfunction()

foreach(t tst_client tst_stream tst_session tst_jsonmodel tst_livequery tst_app)
  taos_qt_test(${t})
endforeach()

# ---- app conventions: build a dummy app through taos_add_native_app --------
taos_add_native_app(
  APP_ID my.taos.dummy
  DISPLAY_NAME "Dummy App"
  COMMENT "Test app"
  SOURCES dummyapp/main.cpp)
target_link_libraries(taos-dummy PRIVATE Qt6::Gui)
add_test(NAME dummyapp-runs COMMAND taos-dummy)
set_tests_properties(dummyapp-runs PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
add_test(NAME dummyapp-desktop-file
         COMMAND ${CMAKE_COMMAND} -DDESKTOP=${CMAKE_CURRENT_BINARY_DIR}/my.taos.dummy.desktop
                 -P ${CMAKE_CURRENT_SOURCE_DIR}/check_desktop.cmake)
# A bad id must be refused at configure time.
add_test(NAME bad-app-id-is-refused
         COMMAND ${CMAKE_COMMAND} -DTEMPLATE_DIR=${TAOS_APP_TEMPLATE_DIR}
                 -DMODULE_DIR=${CMAKE_CURRENT_SOURCE_DIR}/../cmake
                 -P ${CMAKE_CURRENT_SOURCE_DIR}/check_bad_id.cmake)
