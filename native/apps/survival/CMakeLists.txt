# SPDX-License-Identifier: AGPL-3.0-or-later
# Survival Studio: offline library (ZIM), offline maps (PMTiles), taOStak awareness and kits.
# Needs libzim (GPL-2.0-or-later, from the distribution: libzim-dev) and zlib. Without them this directory is skipped
# and the rest of the apps build as before.
find_package(ZLIB QUIET)
find_path(TAOS_ZIM_INCLUDE_DIR zim/archive.h)
find_library(TAOS_ZIM_LIBRARY NAMES zim)
if(NOT TAOS_ZIM_INCLUDE_DIR OR NOT TAOS_ZIM_LIBRARY OR NOT ZLIB_FOUND)
  message(STATUS "survival: libzim or zlib not found, Survival Studio is not built (apt install libzim-dev zlib1g-dev)")
  return()
endif()
find_package(Qt6 6.8 REQUIRED COMPONENTS Qml Quick QuickControls2 Network Gui Concurrent Test)
find_package(Qt6 6.8 QUIET COMPONENTS Positioning)
get_filename_component(TAOS_ZIM_LIBDIR ${TAOS_ZIM_LIBRARY} DIRECTORY)

# The engines: no Qt Quick, shared by the window, the CLI and the tests.
add_library(taos_survival_core STATIC
  engine/zimarchive.cpp engine/articlehtml.cpp engine/packmanager.cpp engine/radiodetector.cpp engine/pmtiles.cpp
  engine/mapdata.cpp engine/kit.cpp engine/statestore.cpp engine/paths.cpp engine/meshinterface.h
  engine/meshclient.cpp engine/socketmeshbackend.cpp engine/meshprefs.cpp engine/qrcode.cpp engine/firmwareclient.cpp engine/meshformat.h)
target_include_directories(taos_survival_core PUBLIC engine ${TAOS_ZIM_INCLUDE_DIR})
target_link_libraries(taos_survival_core PUBLIC Qt6::Core Qt6::Gui Qt6::Network Qt6::Concurrent ${TAOS_ZIM_LIBRARY} ZLIB::ZLIB)
set_target_properties(taos_survival_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# libzim pulls in libxapian; when libzim sits outside the system path (a private copy) the linker needs to see it
function(taos_survival_rpath target)
  # $ORIGIN/../lib is where install-apps.sh puts a private copy of libzim and libxapian
  if(TAOS_ZIM_LIBDIR MATCHES "^/usr/lib")
    set_target_properties(${target} PROPERTIES BUILD_RPATH "\$ORIGIN/../lib")
  else()
    # a private copy: DT_RPATH (not RUNPATH) so that it also finds libxapian for libzim, and the linker must see it
    set_target_properties(${target} PROPERTIES BUILD_RPATH "\$ORIGIN/../lib;${TAOS_ZIM_LIBDIR}")
    target_link_options(${target} PRIVATE "LINKER:--disable-new-dtags" "LINKER:-rpath-link,${TAOS_ZIM_LIBDIR}")
  endif()
endfunction()

set(TAOS_SURVIVAL_DIR ${CMAKE_CURRENT_SOURCE_DIR})
set(TAOS_SURVIVAL_CATALOG catalog/packs.json catalog/kits.json)
function(taos_survival_catalog target)
  qt_add_resources(${target} "${target}_catalog" PREFIX "/taos-survival/catalog" BASE ${TAOS_SURVIVAL_DIR}/catalog
                   FILES ${TAOS_SURVIVAL_DIR}/catalog/packs.json ${TAOS_SURVIVAL_DIR}/catalog/kits.json)
endfunction()

add_executable(taos-survival-cli cli/survival_cli.cpp)
target_link_libraries(taos-survival-cli PRIVATE taos_survival_core)
taos_survival_rpath(taos-survival-cli)
taos_survival_catalog(taos-survival-cli)

# ---- the app ---------------------------------------------------------------------------------------------------
set(TAOS_SURVIVAL_QML Main.qml PackList.qml ArticleView.qml LibraryPage.qml MapStage.qml StagePage.qml MapPage.qml TakPage.qml KitPage.qml
    TakLabel.qml QrCode.qml TakBanner.qml TakAssign.qml TakPeople.qml TakPerson.qml TakRadios.qml TakMessages.qml TakThread.qml TakSettings.qml TakFirmware.qml)
set(TAOS_SURVIVAL_QML_ABS)
foreach(f ${TAOS_SURVIVAL_QML})
  list(APPEND TAOS_SURVIVAL_QML_ABS ${CMAKE_CURRENT_SOURCE_DIR}/qml/${f})
endforeach()
add_library(taos_survival_faces STATIC src/backend.cpp src/mapview.cpp src/takengine.cpp)
target_include_directories(taos_survival_faces PUBLIC src)
target_link_libraries(taos_survival_faces PUBLIC taos_survival_core Qt6::Quick Qt6::Qml)
if(TARGET Qt6::Positioning)
  target_link_libraries(taos_survival_faces PUBLIC Qt6::Positioning)
  target_compile_definitions(taos_survival_faces PUBLIC TAOS_SURVIVAL_POSITIONING=1)
endif()
taos_add_native_app(
  APP_ID my.taos.survival
  DISPLAY_NAME "Survival Studio"
  COMMENT "Offline library, offline maps, mesh awareness and kits"
  CATEGORIES Utility Education
  SOURCES src/main.cpp
  LIBS taos_survival_faces Qt6::Quick Qt6::QuickControls2)
qt_add_resources(taos-survival "taos-survival_qml" PREFIX "/my.taos.survival" BASE ${CMAKE_CURRENT_SOURCE_DIR}/qml FILES ${TAOS_SURVIVAL_QML_ABS})
taos_survival_catalog(taos-survival)
taos_apps_use_common(taos-survival)
taos_survival_rpath(taos-survival)
set(TAOS_SURVIVAL_QML ${TAOS_SURVIVAL_QML} PARENT_SCOPE)
add_subdirectory(tests)
