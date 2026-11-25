# SPDX-License-Identifier: AGPL-3.0-or-later
# Code shared by the shell components: the theme system (tokens, validation, catalog,
# appearance.json, live reload), the glass numbers and the taos-theme tool.
add_library(taos_common STATIC
    theme.cpp theme.h glass.cpp glass.h contrast.cpp contrast.h
    themedef.cpp themedef.h themecatalog.cpp themecatalog.h
    appearance.cpp appearance.h thememanager.cpp thememanager.h
    cli/themecli.cpp cli/themecli.h)
target_include_directories(taos_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/cli)
target_link_libraries(taos_common PUBLIC Qt6::Core Qt6::Gui)

# The one way a shell component starts an application: clean environment, own systemd scope. Qt Core only.
add_library(taos_applaunch STATIC applaunch.cpp applaunch.h)
target_include_directories(taos_applaunch PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(taos_applaunch PUBLIC Qt6::Core)
set_target_properties(taos_applaunch PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(taos_common PUBLIC taos_applaunch)

# The shipped themes travel inside every binary (":/taos/themes"), so nothing needs installing to run.
file(GLOB TAOS_THEME_FILES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/resources/themes/*/theme.json)
qt_add_resources(taos_common taos_themes PREFIX /taos BASE ${CMAKE_CURRENT_SOURCE_DIR}/resources
    FILES ${TAOS_THEME_FILES})

add_executable(taos-theme cli/main.cpp)
target_link_libraries(taos-theme PRIVATE taos_common)
install(TARGETS taos-theme RUNTIME DESTINATION bin)
install(DIRECTORY resources/themes/ DESTINATION share/taos/themes FILES_MATCHING PATTERN "theme.json")

enable_testing()
function(taos_common_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taos_common Qt6::Test Qt6::Quick Qt6::Qml)
    target_compile_definitions(${name} PRIVATE TAOS_COMMON_DIR="${CMAKE_CURRENT_SOURCE_DIR}" TAOS_THEME_BIN="$<TARGET_FILE:taos-theme>")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 120)
endfunction()
if(TARGET Qt6::Test AND TARGET Qt6::Quick AND TARGET Qt6::Qml)
    taos_common_test(test_themes)
    taos_common_test(test_appearance)
    taos_common_test(test_thememanager)
    taos_common_test(test_themecli)
    taos_common_test(test_skinlayout)
    taos_common_test(test_applaunch)
endif()
