# taos-window: the window engine (minimise, restore, full size, close, focus, list, subscribe) and the
# shared core library. The core is also built into the foundation (taosqt) for the title bar.
add_subdirectory(core)

add_executable(taos-window cli/main.cpp)
target_link_libraries(taos-window PRIVATE taos_window_core)
install(TARGETS taos-window RUNTIME DESTINATION bin)
install(FILES sway/46-taos-window.conf DESTINATION share/taos/sway.d)
install(PROGRAMS session/taos-window-session-hook DESTINATION libexec/taos)

add_test(NAME sway-config
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/check-sway-config.sh ${CMAKE_CURRENT_SOURCE_DIR}/.. 
                 ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/base-reserved.conf)
set_tests_properties(sway-config PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
add_test(NAME window-session-hook
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/test-session-hook.sh
                 ${CMAKE_CURRENT_SOURCE_DIR}/session/taos-window-session-hook $<TARGET_FILE:taos-theme>)
set_tests_properties(window-session-hook PROPERTIES TIMEOUT 60)
add_test(NAME session-env-snippet
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/check-session-env.sh ${CMAKE_CURRENT_SOURCE_DIR}/../..)
# Red run: a duplicate of a reserved key must be caught.
add_test(NAME sway-config-catches-duplicates
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/check-sway-config.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dupsnippet
                 ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/base-reserved.conf)
set_tests_properties(sway-config-catches-duplicates PROPERTIES WILL_FAIL TRUE SKIP_RETURN_CODE 77 TIMEOUT 60)
# Red run: the launcher binds Mod4+space, which the base config binds to `floating toggle` until the installer moves it.
add_test(NAME sway-config-needs-floating-rebind
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/check-sway-config.sh ${CMAKE_CURRENT_SOURCE_DIR}/.. 
                 ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/base-preinstall.conf)
set_tests_properties(sway-config-needs-floating-rebind PROPERTIES WILL_FAIL TRUE SKIP_RETURN_CODE 77 TIMEOUT 60)
if(TARGET Qt6::Test)
    function(taos_window_test name)
        qt_add_executable(${name} tests/${name}.cpp)
        target_link_libraries(${name} PRIVATE taos_window_core Qt6::Test)
        target_compile_definitions(${name} PRIVATE TAOS_WINDOW_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures"
                                  TAOS_WINDOW_BIN="$<TARGET_FILE:taos-window>" TAOS_WINDOW_SNIPPET="${CMAKE_CURRENT_SOURCE_DIR}/sway/46-taos-window.conf")
        add_test(NAME ${name} COMMAND ${name})
        set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 180)
    endfunction()
    taos_window_test(test_windowcore)
    # The integration test runs a real (headless) sway: set TAOS_TEST_SWAY to the sway binary (default: sway on
    # PATH) and TAOS_TEST_SWAY_LIBS to extra library directories when it is not installed.
    add_executable(taos-window-dummy tests/dummywindow.cpp)
    target_link_libraries(taos-window-dummy PRIVATE Qt6::Gui)
    taos_window_test(test_window_sway)
    target_compile_definitions(test_window_sway PRIVATE TAOS_WINDOW_DUMMY="$<TARGET_FILE:taos-window-dummy>")
    add_dependencies(test_window_sway taos-window taos-window-dummy)
endif()
