# taos-native-demo: signs in and lists notifications live. Needs Qt Quick.
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

taos_add_native_app(
  APP_ID my.taos.demo
  EXECUTABLE taos-native-demo
  DISPLAY_NAME "taOS Native Demo"
  COMMENT "Foundation demo: live notifications"
  SOURCES main.cpp
  LIBS Qt6::Quick Qt6::QuickControls2)

qt_add_resources(taos-native-demo "qml"
  PREFIX "/"
  BASE ${CMAKE_CURRENT_SOURCE_DIR}/..
  FILES demo/main.qml style/Taos/Style/qmldir style/Taos/Style/TaosStyle.qml
        style/Taos/Style/TaosWindow.qml)
