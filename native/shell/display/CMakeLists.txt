# taos-display: native resolution, best refresh and per person scale. The logic
# lives in a static library so the bar's Display singleton and the tests link
# the very same code the tool uses.
add_library(taosdisplay_core STATIC
    src/displaymodel.cpp src/displaymodel.h
    src/displayconfig.cpp src/displayconfig.h
    src/swayrunner.cpp src/swayrunner.h
    src/displaycontroller.cpp src/displaycontroller.h
    src/outputwatcher.cpp src/outputwatcher.h)
target_include_directories(taosdisplay_core PUBLIC src)
target_link_libraries(taosdisplay_core PUBLIC Qt6::Core)

qt_add_executable(taos-display src/main.cpp)
target_link_libraries(taos-display PRIVATE taosdisplay_core)
install(TARGETS taos-display RUNTIME DESTINATION bin)

function(taos_display_test name)
    qt_add_executable(${name} tests/${name}.cpp tests/fakesway.h)
    target_link_libraries(${name} PRIVATE taosdisplay_core Qt6::Test)
    target_compile_definitions(${name} PRIVATE TAOS_DISPLAY_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
    add_test(NAME ${name} COMMAND ${name})
    set_tests_properties(${name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;LC_ALL=C")
endfunction()
taos_display_test(test_displaymodel)
taos_display_test(test_displayconfig)
taos_display_test(test_displaycontroller)
