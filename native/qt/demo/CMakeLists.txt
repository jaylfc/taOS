# taos-native-demo: signs in and lists notifications live. A pure QML app: the
# entry point is generated. Needs Qt Quick.
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

taos_add_native_app(
  APP_ID my.taos.demo
  EXECUTABLE taos-native-demo
  DISPLAY_NAME "taOS Native Demo"
  COMMENT "Foundation demo: live notifications"
  QML_MAIN main.qml
  LIBS Qt6::Quick Qt6::QuickControls2)
