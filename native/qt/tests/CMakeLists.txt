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
