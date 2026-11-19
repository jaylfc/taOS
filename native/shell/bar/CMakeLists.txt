find_package(PkgConfig REQUIRED)
pkg_check_modules(PULSE REQUIRED IMPORTED_TARGET libpulse)

# taos-bar: the status bar. The data classes live in a static library so the
# unit tests link the very same code the binary uses.
add_library(taosbar_core STATIC
    src/clock.cpp src/clock.h
    src/network.cpp src/network.h
    src/dbusutil.cpp src/dbusutil.h
    src/nmclient.cpp src/nmclient.h
    src/bluezclient.cpp src/bluezclient.h
    src/audiostate.cpp src/audiotypes.h src/audiobackend.h
    src/audio.cpp src/audio.h
    src/pulsebackend.cpp src/pulsebackend.h
    src/baripc.cpp src/baripc.h
    src/battery.cpp src/battery.h
    src/launcher.cpp src/launcher.h)
target_include_directories(taosbar_core PUBLIC src)
target_link_libraries(taosbar_core PUBLIC taos_common Qt6::Core Qt6::Gui Qt6::DBus Qt6::Network PkgConfig::PULSE)
target_compile_definitions(taosbar_core PUBLIC TAOS_BAR_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-bar src/main.cpp src/barmanager.cpp src/barmanager.h src/menuhost.cpp src/menuhost.h)
qt_add_resources(taos-bar bar_resources
    PREFIX /taos/bar
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Bar.qml
        qml/BarItem.qml
        qml/NetworkIcon.qml
        qml/VolumeIcon.qml
        qml/BatteryIcon.qml
        qml/NetMenu.qml
        qml/MenuPanel.qml
        qml/MenuRow.qml
        qml/MenuSwitch.qml
        qml/PillField.qml
        qml/Spinner.qml
        qml/SignalGlyph.qml
        qml/LockGlyph.qml
        qml/CheckGlyph.qml
        qml/BluetoothGlyph.qml
        qml/AudioMenu.qml
        qml/MenuFrame.qml
        qml/LevelSlider.qml
        qml/LevelMeter.qml
        qml/DeviceGlyph.qml
        qml/Osd.qml)
qt_add_resources(taos-bar bar_theme
    PREFIX /taos/bar
    BASE ${TAOS_COMMON_DIR}
    FILES ${TAOS_COMMON_DIR}/resources/themes/taos-default/theme.json)
target_link_libraries(taos-bar PRIVATE taosbar_core Qt6::Quick Qt6::Qml LayerShellQt::Interface)
install(TARGETS taos-bar RUNTIME DESTINATION bin)

function(taos_bar_test name)
    qt_add_executable(${name} tests/${name}.cpp tests/fakeaudio.h)
    target_link_libraries(${name} PRIVATE taosbar_core Qt6::Test)
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C")
endfunction()
taos_bar_test(test_clock)
taos_bar_test(test_network)
taos_bar_test(test_battery)
taos_bar_test(test_theme)
taos_bar_test(test_audiostate)
taos_bar_test(test_audio)
taos_bar_test(test_baripc)
taos_bar_test(test_pulsebackend)
taos_bar_test(test_nmclient)
taos_bar_test(test_bluezclient)
find_program(TAOS_DBUS_DAEMON dbus-daemon)
foreach(t test_nmclient test_bluezclient)
    target_link_libraries(${t} PRIVATE Qt6::DBus)
    target_compile_definitions(${t} PRIVATE TAOS_DBUS_DAEMON="${TAOS_DBUS_DAEMON}")
endforeach()
target_compile_definitions(test_theme PRIVATE TAOS_BAR_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}" TAOS_COMMON_DIR="${TAOS_COMMON_DIR}")

# Dev tool: renders the bar and its menus with fake data to a PNG (see tests/qmlshot.cpp).
qt_add_executable(qmlshot tests/qmlshot.cpp tests/fakeaudio.h src/menuhost.cpp src/menuhost.h)
target_link_libraries(qmlshot PRIVATE taosbar_core Qt6::Quick Qt6::Qml Qt6::Test Qt6::DBus)
target_include_directories(qmlshot PRIVATE tests)
target_compile_definitions(qmlshot PRIVATE TAOS_DBUS_DAEMON="${TAOS_DBUS_DAEMON}" TAOS_BAR_QML_DIR="${CMAKE_CURRENT_SOURCE_DIR}/qml" TAOS_COMMON_DIR="${TAOS_COMMON_DIR}")
