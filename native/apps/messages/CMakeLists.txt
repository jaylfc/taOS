# SPDX-License-Identifier: AGPL-3.0-or-later
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2 Gui)

set(MESSAGES_QML Main.qml ChannelList.qml Conversation.qml MessageRow.qml Composer.qml Switcher.qml MIcon.qml MEmpty.qml)
set(MESSAGES_CPP chatlogic.cpp chatlogic.h chatmodels.cpp chatmodels.h chatengine.cpp chatengine.h)

# The engine and its models, linked into the app and into the tests.
add_library(taos_messages_engine OBJECT ${MESSAGES_CPP})
target_include_directories(taos_messages_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(taos_messages_engine PUBLIC taosqt Qt6::Core Qt6::Gui Qt6::Qml)
set_target_properties(taos_messages_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

taos_add_native_app(
  APP_ID my.taos.messages
  DISPLAY_NAME "Messages"
  COMMENT "Chat with your agents and channels"
  CATEGORIES Network InstantMessaging
  QML_MAIN Main.qml
  QML_FILES ChannelList.qml Conversation.qml MessageRow.qml Composer.qml Switcher.qml MIcon.qml MEmpty.qml
  LIBS Qt6::Quick Qt6::QuickControls2)
taos_apps_use_common(taos-messages)
target_link_libraries(taos-messages PRIVATE taos_messages_engine)
set(TAOS_MESSAGES_QML ${MESSAGES_QML} PARENT_SCOPE)

# ---- tests -------------------------------------------------------------------------------------
find_package(Qt6 6.8 REQUIRED COMPONENTS Test)
add_executable(tst_messages tests/tst_messages.cpp)
target_link_libraries(tst_messages PRIVATE taos_messages_engine taosqt Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::Qml)
target_include_directories(tst_messages PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/../tests)
target_compile_definitions(tst_messages PRIVATE FAKE_APPS_PY="${CMAKE_CURRENT_SOURCE_DIR}/tests/fake_chat_controller.py")
taos_apps_use_common(tst_messages)
set(_abs)
foreach(f ${MESSAGES_QML})
  list(APPEND _abs ${CMAKE_CURRENT_SOURCE_DIR}/${f})
endforeach()
qt_add_resources(tst_messages "tst_messages_qml" PREFIX "/my.taos.messages" BASE ${CMAKE_CURRENT_SOURCE_DIR} FILES ${_abs})
add_test(NAME apps_tst_messages COMMAND ${CMAKE_COMMAND} -E env XDG_RUNTIME_DIR=${CMAKE_CURRENT_BINARY_DIR}/rt_messages
         QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:tst_messages>)
set_tests_properties(apps_tst_messages PROPERTIES TIMEOUT 300)
