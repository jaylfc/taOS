add_library(activity_core STATIC engine.cpp feed.cpp fmt.cpp types.cpp)
target_link_libraries(activity_core PUBLIC taosqt Qt6::Core Qt6::Qml)
target_include_directories(activity_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(activity_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

set(QML ${CMAKE_CURRENT_SOURCE_DIR}/../qml)
file(GLOB ACTIVITY_QML CONFIGURE_DEPENDS ${QML}/*.qml)
list(REMOVE_ITEM ACTIVITY_QML ${QML}/Main.qml)
taos_add_native_app(
  APP_ID my.taos.dashboard
  EXECUTABLE taos-dashboard
  DISPLAY_NAME Activity
  COMMENT "Live system activity: CPU, memory, GPU, models, cluster and scheduler"
  CATEGORIES System Monitor
  QML_MAIN ${QML}/Main.qml
  QML_FILES ${ACTIVITY_QML} ${QML}/qmldir
  SOURCES register.cpp
  LIBS activity_core Qt6::Quick Qt6::QuickControls2)
# Needed so the registration unit in an executable is not dropped.
target_link_libraries(taos-dashboard PRIVATE Qt6::Qml)
