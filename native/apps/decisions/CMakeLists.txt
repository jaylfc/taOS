find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

taos_add_native_app(
  APP_ID my.taos.decisions
  DISPLAY_NAME "Decisions"
  COMMENT "Questions agents ask you, and access requests to approve"
  QML_MAIN Main.qml
  QML_FILES OptionRow.qml DecisionCard.qml ArchiveCard.qml RequestCards.qml
  LIBS Qt6::Quick Qt6::QuickControls2)
taos_apps_use_common(taos-decisions)
