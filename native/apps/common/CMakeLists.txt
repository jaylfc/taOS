# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared pieces of the native apps: the notification store (C++, an OBJECT
# library linked straight into each app so its QML registration is not dropped)
# and the shared QML module `Taos.Apps` (consent card, glass panels, motion).
add_library(taos_apps_common OBJECT notificationfeed.cpp notificationfeed.h)
target_include_directories(taos_apps_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(taos_apps_common PUBLIC taosqt Qt6::Core Qt6::Gui Qt6::Qml)
set_target_properties(taos_apps_common PROPERTIES POSITION_INDEPENDENT_CODE ON)

set(TAOS_APPS_QML_DIR ${CMAKE_CURRENT_SOURCE_DIR}/qml)
set(TAOS_APPS_QML_FILES
  qmldir Look.qml Format.qml TaosText.qml Backdrop.qml GlassPanel.qml Glyph.qml Chip.qml TaosButton.qml TaosField.qml TaosArea.qml TaosCombo.qml TaosCheck.qml EmptyState.qml SignInForm.qml SignInPopover.qml
  ScopeBadge.qml ConsentCard.qml SignInCard.qml Logic.js
  AppTile.qml CornerCaps.qml CoverArt.qml PillButton.qml SidebarItem.qml Skeleton.qml SectionHeader.qml ShelfCard.qml
  CompactRow.qml Hero.qml DetailHeader.qml
  Segmented.qml ProgressTrack.qml BottomTabBar.qml ListRow.qml StudioAgentPanel.qml StableModel.qml)
set(TAOS_APPS_QML_ABS)
foreach(f ${TAOS_APPS_QML_FILES})
  list(APPEND TAOS_APPS_QML_ABS ${TAOS_APPS_QML_DIR}/Taos/Apps/${f})
endforeach()
set(TAOS_APPS_QML_ABS ${TAOS_APPS_QML_ABS} PARENT_SCOPE)
set(TAOS_APPS_QML_DIR ${TAOS_APPS_QML_DIR} PARENT_SCOPE)

# Adds the shared QML module to an app (or test) target; the engine already
# imports qrc:/taos/qml, as for Taos.Style.
function(taos_apps_use_common target)
  target_link_libraries(${target} PRIVATE taos_apps_common)
  qt_add_resources(${target} "${target}_apps_common" PREFIX "/taos/qml" BASE ${TAOS_APPS_QML_DIR}
                   FILES ${TAOS_APPS_QML_ABS})
endfunction()
