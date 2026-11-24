# The web apps engine: taos_webapp_core (Qt Core, Gui for QImage, Network) and the CLI taos-webapp.
# Standalone:   cmake -S native/shell/webapp -B build -G Ninja -DCMAKE_PREFIX_PATH=<qt>
# From the apps project (the Store links the core): add_subdirectory(.../shell/webapp webapp).
cmake_minimum_required(VERSION 3.21)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(taos-webapp VERSION 0.1.0 LANGUAGES CXX)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_AUTOMOC ON)
  find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Test)
  enable_testing()
  add_compile_definitions(QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII QT_NO_URL_CAST_FROM_STRING)
else()
  find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Test)
endif()

add_library(taos_webapp_core STATIC src/webapp.cpp src/webapp.h)
target_include_directories(taos_webapp_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(taos_webapp_core PUBLIC Qt6::Core Qt6::Gui Qt6::Network)
set_target_properties(taos_webapp_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_executable(taos-webapp src/main.cpp)
target_link_libraries(taos-webapp PRIVATE taos_webapp_core)
install(TARGETS taos-webapp RUNTIME DESTINATION bin)

add_executable(tst_webapp tests/tst_webapp.cpp)
target_link_libraries(tst_webapp PRIVATE taos_webapp_core Qt6::Test)
target_compile_definitions(tst_webapp PRIVATE FAKE_BROWSER="${CMAKE_CURRENT_SOURCE_DIR}/tests/fakebrowser.sh"
                           WEBAPP_CLI="$<TARGET_FILE:taos-webapp>")
add_dependencies(tst_webapp taos-webapp)
add_test(NAME webapp_core COMMAND ${CMAKE_COMMAND} -E env QT_QPA_PLATFORM=offscreen $<TARGET_FILE:tst_webapp>)
set_tests_properties(webapp_core PROPERTIES TIMEOUT 120)
