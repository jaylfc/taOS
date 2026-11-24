# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-frame: window buttons (close, minimise, full size) for windows that cannot draw their own header, first the
# web app windows. sway draws the title bar (it reserves the space and moves the window); this draws the three buttons
# over its corner as small layer-shell surfaces and acts through taos_window_core.
find_package(Qt6 REQUIRED COMPONENTS Quick Qml)

add_library(taosframe_core STATIC
    src/frameconfig.cpp src/frameconfig.h src/frameplan.cpp src/frameplan.h
    src/framecontroller.cpp src/framecontroller.h)
target_include_directories(taosframe_core PUBLIC src)
target_link_libraries(taosframe_core PUBLIC taos_common taos_window_core Qt6::Core Qt6::Gui Qt6::Network)
target_compile_definitions(taosframe_core PUBLIC TAOS_FRAME_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-frame src/main.cpp src/stripmanager.cpp src/stripmanager.h)
qt_add_resources(taos-frame frame_resources
    PREFIX /taos/frame
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES qml/Strip.qml qml/StripButton.qml qml/StripTip.qml qml/FrameMenu.qml)
target_link_libraries(taos-frame PRIVATE taosframe_core Qt6::Quick Qt6::Qml LayerShellQt::Interface)
install(TARGETS taos-frame RUNTIME DESTINATION bin)
install(FILES sway/49-taos-frame.conf DESTINATION share/taos/sway.d)

function(taos_frame_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosframe_core Qt6::Test Qt6::Quick Qt6::Qml)
    target_compile_definitions(${name} PRIVATE TAOS_FRAME_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures"
        TAOS_FRAME_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/qml" TAOS_FRAME_SNIPPET="${CMAKE_CURRENT_SOURCE_DIR}/sway/49-taos-frame.conf")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 180)
endfunction()

taos_frame_test(test_frameplan)

# Integration on a headless sway: set TAOS_TEST_SWAY (default: sway on PATH) and TAOS_TEST_SWAY_LIBS when it is not installed.
taos_frame_test(test_frame_sway)
target_compile_definitions(test_frame_sway PRIVATE TAOS_FRAME_BIN="$<TARGET_FILE:taos-frame>" TAOS_WINDOW_BIN="$<TARGET_FILE:taos-window>")
add_dependencies(test_frame_sway taos-frame taos-window)
set_tests_properties(test_frame_sway PROPERTIES TIMEOUT 240)

taos_frame_test(test_frameqml)
# Dev tool: renders a framed window offscreen (tests/frameshot.cpp).
qt_add_executable(frameshot tests/frameshot.cpp)
target_link_libraries(frameshot PRIVATE taosframe_core Qt6::Quick Qt6::Qml Qt6::Test)
target_compile_definitions(frameshot PRIVATE TAOS_FRAME_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/qml")

# A tiny wlr-virtual-pointer client so the integration test can really drag a title bar (sway's seat commands cannot
# move the pointer to start an interactive move). Needs wayland-scanner and libwayland-client; without them the drag
# test skips.
find_program(TAOS_WAYLAND_SCANNER wayland-scanner)
find_package(PkgConfig QUIET)
if(TAOS_WAYLAND_SCANNER AND PkgConfig_FOUND)
    pkg_check_modules(WAYLAND_CLIENT QUIET IMPORTED_TARGET wayland-client)
endif()
if(TAOS_WAYLAND_SCANNER AND WAYLAND_CLIENT_FOUND)
    enable_language(C)
    set(vp_xml ${CMAKE_CURRENT_SOURCE_DIR}/tests/vptool/wlr-virtual-pointer-unstable-v1.xml)
    set(vp_gen ${CMAKE_CURRENT_BINARY_DIR}/vptool)
    file(MAKE_DIRECTORY ${vp_gen})
    add_custom_command(OUTPUT ${vp_gen}/wlr-virtual-pointer-unstable-v1-client-protocol.h ${vp_gen}/wlr-virtual-pointer-unstable-v1-protocol.c
        COMMAND ${TAOS_WAYLAND_SCANNER} client-header ${vp_xml} ${vp_gen}/wlr-virtual-pointer-unstable-v1-client-protocol.h
        COMMAND ${TAOS_WAYLAND_SCANNER} private-code ${vp_xml} ${vp_gen}/wlr-virtual-pointer-unstable-v1-protocol.c
        DEPENDS ${vp_xml})
    add_executable(taos-frame-vptool tests/vptool/vptool.c ${vp_gen}/wlr-virtual-pointer-unstable-v1-protocol.c
        ${vp_gen}/wlr-virtual-pointer-unstable-v1-client-protocol.h)
    target_include_directories(taos-frame-vptool PRIVATE ${vp_gen})
    target_link_libraries(taos-frame-vptool PRIVATE PkgConfig::WAYLAND_CLIENT)
    target_compile_definitions(test_frame_sway PRIVATE TAOS_FRAME_VPTOOL="$<TARGET_FILE:taos-frame-vptool>")
    add_dependencies(test_frame_sway taos-frame-vptool)
endif()
