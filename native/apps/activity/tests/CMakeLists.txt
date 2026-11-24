# SPDX-License-Identifier: AGPL-3.0-or-later
set(FAKE_BASE_PY ${CMAKE_CURRENT_SOURCE_DIR}/../../../testing/fake_controller.py)

add_executable(activity-signin signin_tool.cpp)
target_link_libraries(activity-signin PRIVATE taosqt Qt6::Core)

function(activity_test name)
  add_executable(${name} ${name}.cpp)
  target_link_libraries(${name} PRIVATE activity_core Qt6::Test)
  target_compile_definitions(${name} PRIVATE
    FAKE_BASE_PY="${FAKE_BASE_PY}" FAKE_ACT_PY="${CMAKE_CURRENT_SOURCE_DIR}/fake_activity.py"
    FAKE_PASSWORD_TEXT=u"hunter2-secret-pw"_s)
  add_test(NAME activity-${name} COMMAND ${name})
  set_tests_properties(activity-${name} PROPERTIES TIMEOUT 240 ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
endfunction()

activity_test(tst_fmt)
activity_test(tst_engine)

add_executable(tst_activity_real tst_activity_real.cpp)
target_link_libraries(tst_activity_real PRIVATE activity_core Qt6::Test)
add_test(NAME activity-real COMMAND tst_activity_real)
set_tests_properties(activity-real PROPERTIES TIMEOUT 240 ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

add_executable(activity-shots shots.cpp)
target_link_libraries(activity-shots PRIVATE activity_core taos_common Qt6::Quick Qt6::Gui)
target_include_directories(activity-shots PRIVATE ${TAOS_QT_DIR}/../shell/common)
