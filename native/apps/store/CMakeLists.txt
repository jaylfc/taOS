# SPDX-License-Identifier: AGPL-3.0-or-later
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

add_subdirectory(core)

set(TAOS_STORE_QML Main.qml AppIcon.qml AppRow.qml StateButton.qml Pill.qml ListPage.qml WebAppsPage.qml ThemesPage.qml
    InstalledPage.qml DiscoverPage.qml DetailPage.qml AddWebsiteDialog.qml)
set(TAOS_STORE_QML_ABS)
foreach(f ${TAOS_STORE_QML})
  list(APPEND TAOS_STORE_QML_ABS ${CMAKE_CURRENT_SOURCE_DIR}/qml/${f})
endforeach()

# main() is the foundation's generated one plus the Store's QML types (see src/main.cpp for why it is not generated).
taos_add_native_app(
  APP_ID my.taos.store
  DISPLAY_NAME "Store"
  COMMENT "Apps, web apps, models and themes for this device"
  CATEGORIES System Utility
  SOURCES src/main.cpp
  LIBS taos_store_core Qt6::Quick Qt6::QuickControls2)
qt_add_resources(taos-store "taos-store_qml" PREFIX "/my.taos.store" BASE ${CMAKE_CURRENT_SOURCE_DIR}/qml FILES ${TAOS_STORE_QML_ABS})
qt_add_resources(taos-store "taos-store_catalog" PREFIX "/my.taos.store/catalog" BASE ${CMAKE_CURRENT_SOURCE_DIR}/catalog
                 FILES ${CMAKE_CURRENT_SOURCE_DIR}/catalog/webapps.json)
taos_apps_use_common(taos-store)
set(TAOS_STORE_QML ${TAOS_STORE_QML} PARENT_SCOPE)
add_subdirectory(tests)
