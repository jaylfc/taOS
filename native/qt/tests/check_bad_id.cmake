# SPDX-License-Identifier: AGPL-3.0-or-later
# Configure a throwaway project that calls taos_add_native_app with a bad id.
set(dir ${CMAKE_CURRENT_LIST_DIR}/bad-id-probe)
file(MAKE_DIRECTORY ${dir})
file(WRITE ${dir}/CMakeLists.txt "
cmake_minimum_required(VERSION 3.21)
project(probe LANGUAGES NONE)
set(TAOS_APP_TEMPLATE_DIR ${TEMPLATE_DIR})
include(${MODULE_DIR}/TaosNativeApp.cmake)
taos_add_native_app(APP_ID com.example.Bad DISPLAY_NAME Bad SOURCES x.cpp)
")
execute_process(COMMAND ${CMAKE_COMMAND} -S ${dir} -B ${dir}/b RESULT_VARIABLE rc ERROR_VARIABLE err OUTPUT_QUIET)
file(REMOVE_RECURSE ${dir})
if(rc EQUAL 0)
  message(FATAL_ERROR "a bad APP_ID was accepted")
endif()
if(NOT err MATCHES "APP_ID must look like")
  message(FATAL_ERROR "wrong failure: ${err}")
endif()
