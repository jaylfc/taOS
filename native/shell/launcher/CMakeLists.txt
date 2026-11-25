# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-launcher: the keyboard summoned search panel, and taos-search, the same engine on the command line.
# The engine (providers, ranking, calculator, desktop entries, frecency, IPC) is a static library with no
# GUI in it, so the unit tests and the CLI link the very code the daemon runs.
add_library(taos_search_core STATIC
    src/types.h src/provider.h
    src/fuzzy.cpp src/fuzzy.h src/desktopentry.cpp src/desktopentry.h
    src/frecency.cpp src/frecency.h src/calc.cpp src/calc.h
    src/providers.cpp src/providers.h src/providers_more.cpp src/providers_more.h
    src/config.cpp src/config.h src/agents.h src/fileindex.cpp src/fileindex.h
    src/engine.cpp src/engine.h
    src/runner.cpp src/runner.h src/jsonio.cpp src/jsonio.h
    src/setup.cpp src/setup.h src/ipc.cpp src/ipc.h
    src/appmenu.cpp src/appmenu.h src/actionmenu.cpp src/actionmenu.h)
target_include_directories(taos_search_core PUBLIC src)
target_link_libraries(taos_search_core PUBLIC Qt6::Core Qt6::Network)
set_target_properties(taos_search_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_executable(taos-search cli/search_main.cpp)
target_link_libraries(taos-search PRIVATE taos_search_core)
install(TARGETS taos-search RUNTIME DESTINATION bin)

# The controller and row model (no window in them) are a library so the tests drive the real thing.
add_library(taos_launcher_ui STATIC src/menunav.cpp src/menunav.h src/menucontroller.cpp src/menucontroller.h src/controller.cpp src/controller.h src/rowmodel.cpp src/rowmodel.h
    src/iconprovider.cpp src/iconprovider.h)
target_include_directories(taos_launcher_ui PUBLIC src)
target_link_libraries(taos_launcher_ui PUBLIC taos_search_core Qt6::Gui Qt6::Quick)

qt_add_executable(taos-launcher src/main.cpp src/host.cpp src/host.h)
qt_add_resources(taos-launcher launcher_resources
    PREFIX /taos/launcher
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Launcher.qml
        qml/Glass.qml
        qml/ResultRow.qml
        qml/ActionsView.qml
        qml/Monogram.qml)
target_link_libraries(taos-launcher PRIVATE taos_launcher_ui taos_search_core taos_common Qt6::Quick Qt6::Qml Qt6::Gui LayerShellQt::Interface)
target_compile_definitions(taos-launcher PRIVATE TAOS_LAUNCHER_VERSION="${PROJECT_VERSION}")
install(TARGETS taos-launcher RUNTIME DESTINATION bin)
install(FILES sway/48-taos-launcher.conf DESTINATION share/taos/sway.d)

# Offscreen renders for review (not installed).
qt_add_executable(taos-launcher-shots EXCLUDE_FROM_ALL tests/render_shots.cpp)
qt_add_resources(taos-launcher-shots shots_resources
    PREFIX /taos/launcher
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES qml/Launcher.qml qml/Glass.qml qml/ResultRow.qml qml/ActionsView.qml qml/Monogram.qml)
target_link_libraries(taos-launcher-shots PRIVATE taos_launcher_ui taos_search_core taos_common Qt6::Quick Qt6::Qml Qt6::Gui)
target_compile_definitions(taos-launcher-shots PRIVATE TAOS_LAUNCHER_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")

# Unit tests.
function(taos_launcher_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taos_launcher_ui taos_search_core Qt6::Test)
    target_compile_definitions(${name} PRIVATE TAOS_LAUNCHER_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C" TIMEOUT 120)
endfunction()
taos_launcher_test(launcher_test_fuzzy)
taos_launcher_test(launcher_test_calc)
taos_launcher_test(launcher_test_desktopentry)
taos_launcher_test(launcher_test_engine)
taos_launcher_test(launcher_test_ipc)
taos_launcher_test(launcher_test_providers)
taos_launcher_test(launcher_test_controller)
taos_launcher_test(launcher_test_appmenu)
taos_launcher_test(launcher_test_icons)
taos_launcher_test(launcher_test_actionmenu)
taos_launcher_test(launcher_test_menunav)
taos_launcher_test(launcher_test_menu)
