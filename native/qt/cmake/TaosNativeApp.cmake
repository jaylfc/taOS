# taos_add_native_app(
#   APP_ID my.taos.notifications        # my.taos.<web registry id>: Wayland app_id, StartupWMClass, icon name
#   DISPLAY_NAME "Notifications"
#   [COMMENT "..."] [CATEGORIES Utility Office] [VERSION 0.1]
#   QML_MAIN Main.qml [QML_FILES Other.qml ...]   # a pure QML app: main() is GENERATED
#   [SOURCES extra.cpp ...] [LIBS ...])           # SOURCES alone (with its own main) also works
#
# With QML_MAIN the function generates main() (Taos::runQmlApp: Client, Session
# on the shared desktop session, QML types, Taos.Style, single instance) and
# embeds the QML files at qrc:/<APP_ID>/.

# Builds the executable `taos-<name>` for APP_ID `my.taos.<name>`, links taosqt,
# and writes + installs `<APP_ID>.desktop` (from app/app.desktop.in) with
# StartupWMClass = APP_ID. The code defines TAOS_APP_ID and TAOS_APP_NAME for
# Taos::App::configure().
function(taos_add_native_app)
  cmake_parse_arguments(A "" "APP_ID;DISPLAY_NAME;COMMENT;EXECUTABLE;VERSION;QML_MAIN" "CATEGORIES;SOURCES;LIBS;QML_FILES" ${ARGN})
  if(NOT A_APP_ID OR NOT A_DISPLAY_NAME OR (NOT A_SOURCES AND NOT A_QML_MAIN))
    message(FATAL_ERROR "taos_add_native_app needs APP_ID, DISPLAY_NAME and QML_MAIN or SOURCES")
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
    set(A_CATEGORIES Utility)
  endif()
  # The .desktop format wants a ";"-terminated list: Utility;Office;
  list(JOIN A_CATEGORIES ";" _cats)
  set(_cats "${_cats};")
  if(NOT A_VERSION)
    set(A_VERSION "0.1")
  endif()
  if(A_QML_MAIN)
    set(APP_ID ${A_APP_ID})
    set(APP_DISPLAY_NAME ${A_DISPLAY_NAME})
    set(APP_VERSION ${A_VERSION})
    get_filename_component(APP_QML_MAIN ${A_QML_MAIN} NAME)
    configure_file(${TAOS_APP_TEMPLATE_DIR}/main.cpp.in ${CMAKE_CURRENT_BINARY_DIR}/${A_EXECUTABLE}_main.cpp @ONLY)
    list(APPEND A_SOURCES ${CMAKE_CURRENT_BINARY_DIR}/${A_EXECUTABLE}_main.cpp)
  endif()
  add_executable(${A_EXECUTABLE} ${A_SOURCES})
  if(A_QML_MAIN)
    get_filename_component(_qml_dir ${A_QML_MAIN} ABSOLUTE)
    get_filename_component(_qml_dir ${_qml_dir} DIRECTORY)
    set(_qml_abs)
    foreach(f ${A_QML_MAIN} ${A_QML_FILES})
      get_filename_component(_a ${f} ABSOLUTE)
      list(APPEND _qml_abs ${_a})
    endforeach()
    qt_add_resources(${A_EXECUTABLE} "${A_EXECUTABLE}_qml" PREFIX "/${A_APP_ID}" BASE ${_qml_dir} FILES ${_qml_abs})
  endif()
  target_link_libraries(${A_EXECUTABLE} PRIVATE taosqt ${A_LIBS})
  target_compile_definitions(${A_EXECUTABLE} PRIVATE TAOS_APP_ID="${A_APP_ID}"
                             TAOS_APP_NAME="${A_DISPLAY_NAME}")
  set(APP_ID ${A_APP_ID})
  set(APP_DISPLAY_NAME ${A_DISPLAY_NAME})
  set(APP_COMMENT ${A_COMMENT})
  set(APP_CATEGORIES "${_cats}")
  set(APP_EXEC ${A_EXECUTABLE})
  configure_file(${TAOS_APP_TEMPLATE_DIR}/app.desktop.in
                 ${CMAKE_CURRENT_BINARY_DIR}/${A_APP_ID}.desktop @ONLY)
  install(TARGETS ${A_EXECUTABLE} RUNTIME DESTINATION bin)
  install(FILES ${CMAKE_CURRENT_BINARY_DIR}/${A_APP_ID}.desktop DESTINATION share/applications)
endfunction()
