# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-dock: the application dock. The logic (desktop entries, model, layout, settings, engines) is a
# static library so the tests link the very code the binary runs.
find_package(Qt6 REQUIRED COMPONENTS Quick Qml)

add_library(taosdock_core STATIC
    src/windowinfo.cpp src/windowinfo.h src/windowengine.h
    src/swayipc.cpp src/swayipc.h src/swayengine.cpp src/swayengine.h
    src/cliengine.cpp src/cliengine.h
    src/desktopentry.cpp src/desktopentry.h
    src/dockconfig.cpp src/dockconfig.h
    src/layout.cpp src/layout.h
    src/dockmodel.cpp src/dockmodel.h
    src/taskbarmodel.cpp src/taskbarmodel.h
    src/dockcontroller.cpp src/dockcontroller.h
    src/dockcli.cpp src/dockcli.h
    src/iconprovider.cpp src/iconprovider.h
    src/desktopmode.cpp src/desktopmode.h
    src/dockipc.cpp src/dockipc.h)
target_include_directories(taosdock_core PUBLIC src)
target_link_libraries(taosdock_core PUBLIC taos_common Qt6::Core Qt6::Gui Qt6::Network Qt6::Quick Qt6::Qml)
target_compile_definitions(taosdock_core PUBLIC TAOS_DOCK_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-dock src/main.cpp src/dockmanager.cpp src/dockmanager.h)
qt_add_resources(taos-dock dock_resources
    PREFIX /taos/dock
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Dock.qml
        qml/Taskbar.qml
        qml/TaskbarButton.qml
        qml/DockItem.qml
        qml/DockMenu.qml
        qml/DockSettings.qml
        qml/Tip.qml
        qml/MenuGlyph.qml)
target_link_libraries(taos-dock PRIVATE taosdock_core LayerShellQt::Interface)
install(TARGETS taos-dock RUNTIME DESTINATION bin)

function(taos_dock_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosdock_core Qt6::Test)
    target_compile_definitions(${name} PRIVATE TAOS_DOCK_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 120)
endfunction()
install(FILES ../sway/47-taos-dock.conf DESTINATION share/taos/sway)

taos_dock_test(test_desktopentry)
taos_dock_test(test_dock_layout)
taos_dock_test(test_dockconfig)
taos_dock_test(test_dockmodel)
taos_dock_test(test_sway_integration)
taos_dock_test(test_modefollow)
taos_dock_test(test_dockmenu)
taos_dock_test(test_taskbarmodel)

# Dev tool: renders the dock, its menu and the settings popover offscreen (tests/dockshot.cpp).
qt_add_executable(dockshot tests/dockshot.cpp)
target_link_libraries(dockshot PRIVATE taosdock_core Qt6::Quick Qt6::Qml)
target_compile_definitions(dockshot PRIVATE TAOS_DOCK_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures"
    TAOS_DOCK_TESTS_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests" TAOS_DOCK_ICONS="${CMAKE_CURRENT_SOURCE_DIR}/../../apps/icons")

qt_add_executable(test_dockqml tests/test_dockqml.cpp)
target_link_libraries(test_dockqml PRIVATE taosdock_core Qt6::Test Qt6::Quick Qt6::Qml)
target_compile_definitions(test_dockqml PRIVATE TAOS_DOCK_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures"
    TAOS_DOCK_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/qml")
add_test(NAME test_dockqml COMMAND test_dockqml)
set_tests_properties(test_dockqml PROPERTIES ENVIRONMENT "LC_ALL=C" TIMEOUT 120)
