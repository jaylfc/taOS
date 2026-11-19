# taos-native-demo: signs in and lists notifications live. Needs Qt Quick.
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

taos_add_native_app(
  APP_ID my.taos.demo
  EXECUTABLE taos-native-demo
  DISPLAY_NAME "taOS Native Demo"
  COMMENT "Foundation demo: live notifications"
  SOURCES main.cpp
  LIBS Qt6::Quick Qt6::QuickControls2)

get_filename_component(QT_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/.. ABSOLUTE)
# Resource paths are relative to BASE: :/demo/main.qml and :/style/Taos/Style/...
qt_add_resources(taos-native-demo "qml"
  PREFIX "/"
  BASE ${QT_ROOT}
  FILES ${CMAKE_CURRENT_SOURCE_DIR}/main.qml
        ${QT_ROOT}/style/Taos/Style/qmldir
        ${QT_ROOT}/style/Taos/Style/TaosStyle.qml
        ${QT_ROOT}/style/Taos/Style/TaosWindow.qml)
