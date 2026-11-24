find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

set(NOTIF_QML Main.qml NotificationRow.qml)
taos_add_native_app(
  APP_ID my.taos.notifications
  DISPLAY_NAME "Notifications"
  COMMENT "Server notifications, consent requests and sign-in codes"
  QML_MAIN Main.qml
  QML_FILES NotificationRow.qml
  LIBS Qt6::Quick Qt6::QuickControls2)
taos_apps_use_common(taos-notifications)
# The same QML list, for the tests that load the app against a fake controller.
set(TAOS_NOTIF_QML ${NOTIF_QML} PARENT_SCOPE)
