# SPDX-License-Identifier: AGPL-3.0-or-later
add_library(taosqt SHARED
  error.cpp client.cpp eventstream.cpp session.cpp desktopsession.cpp desktopsso.cpp api.cpp jsonmodel.cpp livequery.cpp app.cpp)
target_include_directories(taosqt PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_definitions(taosqt PRIVATE TAOSQT_BUILD)
target_link_libraries(taosqt PUBLIC Qt6::Core Qt6::Network PRIVATE taos_client_ffi)
set_target_properties(taosqt PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION 0
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
# Keep the Rust runtime symbols out of the public surface of the .so.
target_link_options(taosqt PRIVATE -Wl,--exclude-libs,ALL)

if(Qt6Qml_FOUND AND Qt6Quick_FOUND)
  target_sources(taosqt PRIVATE qmlregister.cpp appcontext.cpp qmlapp.cpp windowcontrol.cpp)
  target_link_libraries(taosqt PRIVATE taos_window_core)
  target_link_libraries(taosqt PUBLIC Qt6::Qml PRIVATE Qt6::Quick)
  target_compile_definitions(taosqt PUBLIC TAOSQT_WITH_QML)
  if(TARGET taos_common)
    # The shell's Theme and Style (native/shell/common) back the QML TaosStyle.
    target_link_libraries(taosqt PRIVATE taos_common)
    target_compile_definitions(taosqt PRIVATE TAOSQT_WITH_STYLE)
  endif()
endif()
target_link_libraries(taosqt PUBLIC Qt6::Gui)

# The QML style module (import Taos.Style 1.0) travels inside the library, so
# every app finds it at qrc:/taos/qml without its own copy.
qt_add_resources(taosqt "taos_style" PREFIX "/taos/qml" BASE ${CMAKE_CURRENT_SOURCE_DIR}/../style
  FILES ${CMAKE_CURRENT_SOURCE_DIR}/../style/Taos/Style/qmldir
        ${CMAKE_CURRENT_SOURCE_DIR}/../style/Taos/Style/TaosStyle.qml
        ${CMAKE_CURRENT_SOURCE_DIR}/../style/Taos/Style/TaosWindow.qml
        ${CMAKE_CURRENT_SOURCE_DIR}/../style/Taos/Style/TaosTitleBar.qml
        ${CMAKE_CURRENT_SOURCE_DIR}/../style/Taos/Style/TitleButton.qml)
