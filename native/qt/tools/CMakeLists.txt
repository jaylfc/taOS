# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-session: command line face of the desktop's shared sign-in (no QML needed).
add_executable(taos-session taos-session.cpp)
target_link_libraries(taos-session PRIVATE taosqt Qt6::Core)
