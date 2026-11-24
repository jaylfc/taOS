# SPDX-License-Identifier: AGPL-3.0-or-later
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2)

# The engine (list, filters, actions) as an OBJECT library: linked straight into
# the app and the tests so its QML registration is not dropped.
add_library(taos_agents_engine OBJECT src/agentdirectory.cpp src/agentdirectory.h)
target_include_directories(taos_agents_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(taos_agents_engine PUBLIC taosqt Qt6::Core Qt6::Gui Qt6::Qml)
set_target_properties(taos_agents_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

set(TAOS_AGENTS_QML Main.qml AgentRow.qml DetailPane.qml DeviceTag.qml AgentIcon.qml Tone.qml qmldir)
taos_add_native_app(
  APP_ID my.taos.agents
  DISPLAY_NAME "Agents"
  COMMENT "The agents deployed for you, where they run and what they are doing"
  QML_MAIN Main.qml
  QML_FILES AgentRow.qml DetailPane.qml DeviceTag.qml AgentIcon.qml Tone.qml qmldir
  LIBS Qt6::Quick Qt6::QuickControls2 taos_agents_engine)
taos_apps_use_common(taos-agents)
# The same QML list, for the tests that load the app against a fake controller.
set(TAOS_AGENTS_QML ${TAOS_AGENTS_QML} PARENT_SCOPE)
add_subdirectory(tests)
