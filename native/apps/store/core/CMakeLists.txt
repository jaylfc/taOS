# SPDX-License-Identifier: AGPL-3.0-or-later
# taos_store_core: the Store's engine (catalogue, install state machine, web apps and themes services, the apps
# overview of Settings). An OBJECT library, so its QML registration is not dropped. Used by the Store app, its tests
# and Settings (a standalone settings build adds this directory too). Needs taosqt and taos_common in scope.
find_package(Qt6 6.8 REQUIRED COMPONENTS Core Gui Network Qml)

if(NOT TARGET taos_webapp_core)
  add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/../../../shell/webapp webapp)
endif()

set(_S ${CMAKE_CURRENT_LIST_DIR}/../src)
# the person-confirmation gate lives with the app groups engine; the Store is the one process that can mint
set(_AG ${CMAKE_CURRENT_LIST_DIR}/../../../shell/appgroups/src)
add_library(taos_store_core OBJECT
  ${_S}/storecatalog.cpp ${_S}/storecatalog.h ${_S}/webappsservice.cpp ${_S}/webappsservice.h ${_S}/containerappsservice.cpp ${_S}/containerappsservice.h
  ${_S}/storeengine.cpp ${_S}/storeengine.h ${_S}/appsoverview.cpp ${_S}/appsoverview.h
  ${_AG}/owner.cpp ${_AG}/owner.h)
target_include_directories(taos_store_core PUBLIC ${_S} ${_AG})
target_link_libraries(taos_store_core PUBLIC taosqt taos_common taos_webapp_core Qt6::Core Qt6::Gui Qt6::Qml Qt6::Network)
set_target_properties(taos_store_core PROPERTIES POSITION_INDEPENDENT_CODE ON AUTOMOC ON)
