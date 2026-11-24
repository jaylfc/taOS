# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-notifyd: the freedesktop notification server of the session. UI-less (QtCore, QtDBus and
# QtNetwork only). The logic is a static library so the tests link the code the binary runs.
find_package(Qt6 REQUIRED COMPONENTS Core Network DBus Test)

add_library(taosnotifyd_core STATIC
    src/notification.cpp src/notification.h
    src/buffer.cpp src/buffer.h
    src/forwarder.cpp src/forwarder.h
    src/lockgate.cpp src/lockgate.h
    src/service.cpp src/service.h)
target_include_directories(taosnotifyd_core PUBLIC src)
target_link_libraries(taosnotifyd_core PUBLIC Qt6::Core Qt6::Network Qt6::DBus)
target_compile_definitions(taosnotifyd_core PUBLIC TAOS_NOTIFYD_VERSION="${PROJECT_VERSION}")

add_executable(taos-notifyd src/main.cpp)
target_link_libraries(taos-notifyd PRIVATE taosnotifyd_core)
install(TARGETS taos-notifyd RUNTIME DESTINATION bin)

# The unit tests never touch a real session bus: test_service starts its own dbus-daemon on a
# socket inside its temp directory and refuses to run against any other address.
function(taos_notifyd_test name)
    qt_add_executable(${name} tests/${name}.cpp)
    target_link_libraries(${name} PRIVATE taosnotifyd_core Qt6::Test)
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C;DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/taos-notifyd-test"
        TIMEOUT 120)
endfunction()
taos_notifyd_test(test_buffer)
taos_notifyd_test(test_forwarder)
taos_notifyd_test(test_lockgate)
taos_notifyd_test(test_service)

add_test(NAME notifyd-e2e COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e-notifyd.sh $<TARGET_FILE:taos-notifyd>)
set_tests_properties(notifyd-e2e PROPERTIES TIMEOUT 180 LABELS e2e SKIP_RETURN_CODE 77)
