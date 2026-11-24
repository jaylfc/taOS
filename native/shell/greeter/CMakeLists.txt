# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-greeter: the login screen. The logic classes live in a static library so
# the unit tests link the very same code the binary uses.
find_package(Qt6 6.8 REQUIRED COMPONENTS DBus Quick Qml)

add_library(taosgreeter_core STATIC
    src/greetdclient.cpp src/greetdclient.h
    src/usermodel.cpp src/usermodel.h
    src/weathermodel.cpp src/weathermodel.h
    src/poweractions.cpp src/poweractions.h
    src/wallpaperprovider.cpp src/wallpaperprovider.h
    src/layoutplan.cpp src/layoutplan.h
    src/keystate.cpp src/keystate.h
    src/controller.cpp src/controller.h
    src/fakegreetd.cpp src/fakegreetd.h)
target_include_directories(taosgreeter_core PUBLIC src)
target_link_libraries(taosgreeter_core PUBLIC taos_common Qt6::Core Qt6::Gui Qt6::Network Qt6::DBus)
target_compile_definitions(taosgreeter_core PUBLIC TAOS_GREETER_VERSION="${PROJECT_VERSION}")

qt_add_executable(taos-greeter src/main.cpp)
qt_add_resources(taos-greeter greeter_resources
    PREFIX /taos/greeter
    BASE ${CMAKE_CURRENT_SOURCE_DIR}
    FILES
        qml/Main.qml
        qml/GlassPanel.qml
        qml/Avatar.qml
        qml/UserTile.qml
        qml/PasswordField.qml
        qml/PowerButton.qml
        qml/ClockBlock.qml
        qml/WeatherIcon.qml
        qml/Glyphs.js)
target_link_libraries(taos-greeter PRIVATE taosgreeter_core Qt6::Quick Qt6::Qml)
install(TARGETS taos-greeter RUNTIME DESTINATION bin)

function(taos_greeter_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosgreeter_core Qt6::Test)
    target_compile_definitions(${name} PRIVATE
        TAOS_SHELL_DIR="${CMAKE_CURRENT_SOURCE_DIR}/.." TAOS_COMMON_DIR="${TAOS_COMMON_DIR}")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C;TAOS_GREETER_NO_DBUS=1"
        TIMEOUT 120)
endfunction()
taos_greeter_test(test_greetdclient)
taos_greeter_test(test_usermodel)
taos_greeter_test(test_weather)
taos_greeter_test(test_layout)
taos_greeter_test(test_contrast)
taos_greeter_test(test_poweractions)
taos_greeter_test(test_wallpaper)

add_test(NAME weather-cache-script
    COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/../weather/tests/test-weather-cache.sh
            ${CMAKE_CURRENT_SOURCE_DIR}/../weather)
set_tests_properties(weather-cache-script PROPERTIES TIMEOUT 120)

install(FILES ../weather/taos-weather-cache.service ../weather/taos-weather-cache.timer
        DESTINATION lib/systemd/system)
install(PROGRAMS ../weather/taos-weather-cache DESTINATION bin)
