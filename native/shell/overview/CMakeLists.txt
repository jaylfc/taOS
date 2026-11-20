# taos-overview: the Alt+Tab switcher and the workspace overview.
# The logic that needs neither Wayland nor a GUI (sway IPC framing, tree parsing, MRU, filter,
# packing, the control socket) lives in a static library so the unit tests link the very code the
# daemon runs.
find_package(PkgConfig REQUIRED)
pkg_check_modules(WAYLAND_CLIENT REQUIRED IMPORTED_TARGET wayland-client)
find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
enable_language(C)

# Protocol glue, generated at build time from the vendored XML (native/shell/protocols).
set(PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../protocols)
set(GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/protocols)
file(MAKE_DIRECTORY ${GEN_DIR})
set(PROTO_SRCS "")
foreach(p ext-foreign-toplevel-list-v1 ext-image-capture-source-v1 ext-image-copy-capture-v1)
    add_custom_command(
        OUTPUT ${GEN_DIR}/${p}-client-protocol.h ${GEN_DIR}/${p}-protocol.c
        COMMAND ${WAYLAND_SCANNER} client-header ${PROTO_DIR}/${p}.xml ${GEN_DIR}/${p}-client-protocol.h
        COMMAND ${WAYLAND_SCANNER} private-code ${PROTO_DIR}/${p}.xml ${GEN_DIR}/${p}-protocol.c
        DEPENDS ${PROTO_DIR}/${p}.xml
        VERBATIM)
    list(APPEND PROTO_SRCS ${GEN_DIR}/${p}-protocol.c)
endforeach()
add_library(taos_overview_proto STATIC ${PROTO_SRCS})
set_source_files_properties(${PROTO_SRCS} PROPERTIES LANGUAGE C)
target_include_directories(taos_overview_proto PUBLIC ${GEN_DIR})
target_link_libraries(taos_overview_proto PUBLIC PkgConfig::WAYLAND_CLIENT)
set_target_properties(taos_overview_proto PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(taosoverview_core STATIC
    src/swaytree.cpp src/swaytree.h
    src/windowlist.cpp src/windowlist.h
    src/packing.cpp src/packing.h
    src/swayipc.cpp src/swayipc.h
    src/control.cpp src/control.h
    src/appinfo.cpp src/appinfo.h)
target_include_directories(taosoverview_core PUBLIC src)
target_link_libraries(taosoverview_core PUBLIC taos_common Qt6::Core Qt6::Gui Qt6::Network)
target_compile_definitions(taosoverview_core PUBLIC TAOS_OVERVIEW_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-overview
    src/main.cpp
    src/capture.cpp src/capture.h
    src/thumbnails.cpp src/thumbnails.h
    src/windowmodel.cpp src/windowmodel.h
    src/controller.cpp src/controller.h
    src/iconprovider.cpp src/iconprovider.h
    src/overlayhost.cpp src/overlayhost.h
    src/watchdog.cpp src/watchdog.h)
qt_add_resources(taos-overview overview_resources
    PREFIX /taos/overview
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Overlay.qml
        qml/Switcher.qml
        qml/OverviewView.qml
        qml/Glass.qml
        qml/WindowThumb.qml
        qml/AppBadge.qml
        qml/Monogram.qml)
qt_add_resources(taos-overview overview_theme
    PREFIX /taos/overview
    BASE ${TAOS_COMMON_DIR}
    FILES ${TAOS_COMMON_DIR}/resources/themes/taos-default/theme.json)
target_link_libraries(taos-overview PRIVATE taosoverview_core taos_overview_proto Qt6::Quick Qt6::Qml
                      LayerShellQt::Interface)
install(TARGETS taos-overview RUNTIME DESTINATION bin)
install(FILES sway/40-overview.conf DESTINATION share/taos/sway.d)

function(taos_overview_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosoverview_core Qt6::Test)
    target_compile_definitions(${name} PRIVATE TAOS_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 120)
endfunction()
taos_overview_test(test_swaytree)
taos_overview_test(test_windowlist)
taos_overview_test(test_packing)
taos_overview_test(test_ipc)
