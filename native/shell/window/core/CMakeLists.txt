# SPDX-License-Identifier: AGPL-3.0-or-later
# taos_window_core: the window engine without any GUI. The sway IPC client, the window model (the tree as
# windows, with minimised state read from sway marks), the plans for minimise / restore / full size / close /
# focus, and the event follower. Shared by taos-window, the dock, the overview and the title bar in taosqt.
add_library(taos_window_core STATIC
    swayconn.cpp swayconn.h windowmodel.cpp windowmodel.h windowops.cpp windowops.h
    windowevents.cpp windowevents.h desktopmodes.cpp desktopmodes.h)
target_include_directories(taos_window_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_link_libraries(taos_window_core PUBLIC Qt6::Core)
set_target_properties(taos_window_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
