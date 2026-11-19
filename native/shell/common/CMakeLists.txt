# Code shared by the shell components (theme loader today).
add_library(taos_common STATIC theme.cpp theme.h glass.cpp glass.h)
target_include_directories(taos_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(taos_common PUBLIC Qt6::Core Qt6::Gui)
set(TAOS_BUILTIN_THEME_JSON ${CMAKE_CURRENT_SOURCE_DIR}/resources/themes/taos-default/theme.json
    CACHE INTERNAL "built-in default theme")
