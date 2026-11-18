# taos-bar: the status bar. The data classes live in a static library so the
# unit tests link the very same code the binary uses.
add_library(taosbar_core STATIC
    src/clock.cpp src/clock.h
    src/network.cpp src/network.h
    src/volume.cpp src/volume.h
    src/battery.cpp src/battery.h
    src/launcher.cpp src/launcher.h)
target_include_directories(taosbar_core PUBLIC src)
target_link_libraries(taosbar_core PUBLIC taos_common Qt6::Core Qt6::Gui)
target_compile_definitions(taosbar_core PUBLIC TAOS_BAR_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-bar src/main.cpp src/barmanager.cpp src/barmanager.h)
qt_add_resources(taos-bar bar_resources
    PREFIX /taos/bar
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Bar.qml
        qml/BarItem.qml
        qml/NetworkIcon.qml
        qml/VolumeIcon.qml
        qml/BatteryIcon.qml)
qt_add_resources(taos-bar bar_theme
    PREFIX /taos/bar
    BASE ${TAOS_COMMON_DIR}
    FILES ${TAOS_COMMON_DIR}/resources/themes/taos-default/theme.json)
target_link_libraries(taos-bar PRIVATE taosbar_core Qt6::Quick Qt6::Qml LayerShellQt::Interface)
install(TARGETS taos-bar RUNTIME DESTINATION bin)

function(taos_bar_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosbar_core Qt6::Test)
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C")
endfunction()
taos_bar_test(test_clock)
taos_bar_test(test_network)
taos_bar_test(test_battery)
taos_bar_test(test_volume)
taos_bar_test(test_theme)
target_compile_definitions(test_theme PRIVATE TAOS_BAR_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}" TAOS_COMMON_DIR="${TAOS_COMMON_DIR}")
