# SPDX-License-Identifier: AGPL-3.0-or-later
remove_definitions(-DQT_NO_CAST_FROM_ASCII -DQT_NO_CAST_TO_ASCII)
set(SHELL_TESTS ${TAOS_SHELL_DIR})
function(taos_settings_test name)
  add_executable(${name} ${name}.cpp ${SHELL_TESTS}/bar/tests/fakeaudio.h)
  target_link_libraries(${name} PRIVATE settings_core Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::QuickShapes)
  target_include_directories(${name} PRIVATE ${SHELL_TESTS}/display/tests ${SHELL_TESTS}/bar/tests)
  target_compile_definitions(${name} PRIVATE TAOS_DISPLAY_FIXTURES="${SHELL_TESTS}/display/tests/fixtures"
                             SETTINGS_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../qml")
  qt_add_resources(${name} "${name}_qml" PREFIX "/my.taos.settings" BASE ${SETTINGS_RES_BASE} FILES ${SETTINGS_QML_ABS})
endfunction()

taos_settings_test(tst_settings)
add_test(NAME settings_logic COMMAND ${CMAKE_COMMAND} -E env QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:tst_settings>)
set_tests_properties(settings_logic PROPERTIES TIMEOUT 300)

# Renders every first-cut page against fake engines and temporary folders: settings-shots <outdir>
taos_settings_test(settings_shots)
