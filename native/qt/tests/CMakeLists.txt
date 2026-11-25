# SPDX-License-Identifier: AGPL-3.0-or-later
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

foreach(t tst_client tst_stream tst_session tst_jsonmodel tst_livequery tst_app tst_real)
  taos_qt_test(${t})
endforeach()

# The command line face of the shared sign-in, run as a real process.
taos_qt_test(tst_sessioncli)
target_compile_definitions(tst_sessioncli PRIVATE TAOS_SESSION_BIN="$<TARGET_FILE:taos-session>"
                           FAKE_SSO_PY="${CMAKE_CURRENT_SOURCE_DIR}/../../testing/fake_sso.py")
add_dependencies(tst_sessioncli taos-session)

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

# ---- the title bar, rendered offscreen (needs Qt Quick) ---------------------
if(Qt6Quick_FOUND AND Qt6QuickControls2_FOUND AND TARGET taos_common)
  find_package(Qt6 6.8 REQUIRED COMPONENTS Quick QuickControls2)
  add_executable(taos-render-titlebar render_titlebar.cpp)
  target_link_libraries(taos-render-titlebar PRIVATE taosqt taos_common Qt6::Quick Qt6::Qml Qt6::Test)
  add_test(NAME titlebar-renders
           COMMAND ${CMAKE_COMMAND} -DBIN=$<TARGET_FILE:taos-render-titlebar> -DOUT=${CMAKE_CURRENT_BINARY_DIR}/titlebar-shots
                   -P ${CMAKE_CURRENT_SOURCE_DIR}/check_titlebar.cmake)
  set_tests_properties(titlebar-renders PROPERTIES TIMEOUT 120)
endif()
