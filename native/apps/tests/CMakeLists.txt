find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2 Test)
find_program(PYTHON3 python3 REQUIRED)

set(APPS_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(FAKE_APPS_PY ${CMAKE_CURRENT_SOURCE_DIR}/fake_apps_controller.py)

function(taos_apps_test name)
  add_executable(${name} ${name}.cpp)
  target_link_libraries(${name} PRIVATE taosqt Qt6::Test Qt6::Quick Qt6::QuickControls2 Qt6::Qml)
  target_include_directories(${name} PRIVATE ${APPS_DIR}/common)
  target_compile_definitions(${name} PRIVATE FAKE_APPS_PY="${FAKE_APPS_PY}")
  taos_apps_use_common(${name})
  # the apps' own QML, at the same resource prefix the real executables use
  qt_add_resources(${name} "${name}_notifications" PREFIX "/my.taos.notifications" BASE ${APPS_DIR}/notifications
    FILES ${APPS_DIR}/notifications/Main.qml ${APPS_DIR}/notifications/NotificationRow.qml)
  qt_add_resources(${name} "${name}_decisions" PREFIX "/my.taos.decisions" BASE ${APPS_DIR}/decisions
    FILES ${APPS_DIR}/decisions/Main.qml ${APPS_DIR}/decisions/OptionRow.qml ${APPS_DIR}/decisions/DecisionCard.qml
          ${APPS_DIR}/decisions/ArchiveCard.qml ${APPS_DIR}/decisions/RequestCards.qml)
  # a private runtime dir: the desktop session file of another app must not be touched
  add_test(NAME apps_${name} COMMAND ${CMAKE_COMMAND} -E env XDG_RUNTIME_DIR=${CMAKE_CURRENT_BINARY_DIR}/rt_${name}
           QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software $<TARGET_FILE:${name}>)
  set_tests_properties(apps_${name} PROPERTIES TIMEOUT 300)
endfunction()

foreach(t tst_apps_logic tst_apps_ui tst_apps_real)
  taos_apps_test(${t})
endforeach()

add_executable(taos-apps-seed-session seed_session.cpp)
target_link_libraries(taos-apps-seed-session PRIVATE taosqt)
