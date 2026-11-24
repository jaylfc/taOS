cmake_minimum_required(VERSION 3.21)
project(taos-native-settings VERSION 0.1.0 LANGUAGES CXX)

# Standalone build of the Settings app on the foundation (native/qt) and the shell engines (native/shell).
#   cmake -S native/apps/settings -B build -G Ninja -DCMAKE_PREFIX_PATH=<qt> -DTAOS_CARGO_OFFLINE=ON
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

set(TAOS_QT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../qt)
set(TAOS_SHELL_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../shell)
add_subdirectory(${TAOS_QT_DIR} foundation EXCLUDE_FROM_ALL)   # taosqt, taos_common, taos_window_core
set(TAOS_APP_TEMPLATE_DIR ${TAOS_QT_DIR}/app)
list(APPEND CMAKE_MODULE_PATH ${TAOS_QT_DIR}/cmake)
include(TaosNativeApp)

find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network DBus Qml Quick QuickControls2 QuickShapes Test)
find_package(PkgConfig REQUIRED)
pkg_check_modules(PULSE REQUIRED IMPORTED_TARGET libpulse)
enable_testing()

# The shell engines, compiled from their own sources, unmodified: the display engine and the bar's audio,
# NetworkManager and BlueZ models. (There is no standalone models library for them yet; see the PARITY notes.)
set(ENGINE_SOURCES
  ${TAOS_SHELL_DIR}/display/src/displaymodel.cpp ${TAOS_SHELL_DIR}/display/src/displayconfig.cpp
  ${TAOS_SHELL_DIR}/display/src/swayrunner.cpp ${TAOS_SHELL_DIR}/display/src/displaycontroller.cpp
  ${TAOS_SHELL_DIR}/display/src/outputwatcher.cpp ${TAOS_SHELL_DIR}/display/src/displaycontroller.h
  ${TAOS_SHELL_DIR}/display/src/outputwatcher.h ${TAOS_SHELL_DIR}/display/src/swayrunner.h
  ${TAOS_SHELL_DIR}/bar/src/dbusutil.cpp ${TAOS_SHELL_DIR}/bar/src/nmclient.cpp ${TAOS_SHELL_DIR}/bar/src/bluezclient.cpp
  ${TAOS_SHELL_DIR}/bar/src/audiostate.cpp ${TAOS_SHELL_DIR}/bar/src/audio.cpp ${TAOS_SHELL_DIR}/bar/src/pulsebackend.cpp
  ${TAOS_SHELL_DIR}/bar/src/audiobackend.h ${TAOS_SHELL_DIR}/bar/src/pulsebackend.h)
add_library(settings_engines STATIC ${ENGINE_SOURCES})
target_include_directories(settings_engines PUBLIC ${TAOS_SHELL_DIR}/display/src ${TAOS_SHELL_DIR}/bar/src)
target_link_libraries(settings_engines PUBLIC Qt6::Core Qt6::Gui Qt6::DBus Qt6::Network PkgConfig::PULSE taos_common taos_window_core)
set_target_properties(settings_engines PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_compile_definitions(QT_DISABLE_DEPRECATED_BEFORE=0x060800 QT_NO_CAST_FROM_ASCII
                        QT_NO_CAST_TO_ASCII QT_NO_URL_CAST_FROM_STRING)

# Settings > Apps shows the web apps engine and the Store's overview of installed apps.
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../store/core store-core)

add_library(settings_core STATIC
  src/catalog.cpp src/navigator.cpp src/instance.cpp src/filesettings.cpp src/displaypage.cpp
  src/aboutinfo.cpp src/shortcuts.cpp src/context.cpp)
target_include_directories(settings_core PUBLIC src)
target_link_libraries(settings_core PUBLIC settings_engines taos_store_core taosqt Qt6::Core Qt6::Qml Qt6::Network)
set_target_properties(settings_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

file(GLOB SETTINGS_QML CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/qml/*.qml)
set(SETTINGS_QML_ABS ${SETTINGS_QML})
set(SETTINGS_RES_BASE ${CMAKE_CURRENT_SOURCE_DIR}/qml)

taos_add_native_app(
  APP_ID my.taos.settings
  DISPLAY_NAME "Settings"
  COMMENT "System settings: appearance, displays, dock, sound, network and more"
  CATEGORIES Settings System
  SOURCES src/main.cpp
  LIBS settings_core Qt6::Quick Qt6::QuickControls2 Qt6::QuickShapes)
qt_add_resources(taos-settings "settings_qml" PREFIX "/my.taos.settings" BASE ${SETTINGS_RES_BASE} FILES ${SETTINGS_QML_ABS})
install(FILES ${CMAKE_CURRENT_SOURCE_DIR}/icons/my.taos.settings.svg
        DESTINATION share/icons/hicolor/scalable/apps)

add_subdirectory(tests)
