# taos_add_native_app(
#   APP_ID my.taos.notifications        # reverse DNS: the Wayland app_id, StartupWMClass, icon name
#   DISPLAY_NAME "Notifications"
#   [COMMENT "..."] [CATEGORIES "Utility;"]
#   SOURCES main.cpp ...
#   [QML_FILES ...] [LIBS ...])
#
# Builds the executable `taos-<name>` for APP_ID `my.taos.<name>`, links taosqt,
# and writes + installs `<APP_ID>.desktop` (from app/app.desktop.in) with
# StartupWMClass = APP_ID. The code defines TAOS_APP_ID and TAOS_APP_NAME for
# Taos::App::configure().
function(taos_add_native_app)
  cmake_parse_arguments(A "" "APP_ID;DISPLAY_NAME;COMMENT;CATEGORIES;EXECUTABLE" "SOURCES;LIBS" ${ARGN})
  if(NOT A_APP_ID OR NOT A_DISPLAY_NAME OR NOT A_SOURCES)
    message(FATAL_ERROR "taos_add_native_app needs APP_ID, DISPLAY_NAME and SOURCES")
  endif()
  if(NOT A_APP_ID MATCHES "^my\\.taos\\.[a-z][a-z0-9-]*$")
    message(FATAL_ERROR "APP_ID must look like my.taos.<name> (lower case): ${A_APP_ID}")
  endif()
  if(NOT A_EXECUTABLE)
    string(REGEX REPLACE "^my\\.taos\\." "taos-" A_EXECUTABLE ${A_APP_ID})
  endif()
  if(NOT A_COMMENT)
    set(A_COMMENT "${A_DISPLAY_NAME}")
  endif()
  if(NOT A_CATEGORIES)
    set(A_CATEGORIES "Utility;")
  endif()
  add_executable(${A_EXECUTABLE} ${A_SOURCES})
  target_link_libraries(${A_EXECUTABLE} PRIVATE taosqt ${A_LIBS})
  target_compile_definitions(${A_EXECUTABLE} PRIVATE TAOS_APP_ID="${A_APP_ID}"
                             TAOS_APP_NAME="${A_DISPLAY_NAME}")
  set(APP_ID ${A_APP_ID})
  set(APP_DISPLAY_NAME ${A_DISPLAY_NAME})
  set(APP_COMMENT ${A_COMMENT})
  set(APP_CATEGORIES ${A_CATEGORIES})
  set(APP_EXEC ${A_EXECUTABLE})
  configure_file(${TAOS_APP_TEMPLATE_DIR}/app.desktop.in
                 ${CMAKE_CURRENT_BINARY_DIR}/${A_APP_ID}.desktop @ONLY)
  install(TARGETS ${A_EXECUTABLE} RUNTIME DESTINATION bin)
  install(FILES ${CMAKE_CURRENT_BINARY_DIR}/${A_APP_ID}.desktop DESTINATION share/applications)
endfunction()
