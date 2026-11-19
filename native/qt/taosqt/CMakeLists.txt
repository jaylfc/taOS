add_library(taosqt SHARED
  error.cpp client.cpp eventstream.cpp session.cpp api.cpp jsonmodel.cpp livequery.cpp app.cpp)
target_include_directories(taosqt PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_definitions(taosqt PRIVATE TAOSQT_BUILD)
target_link_libraries(taosqt PUBLIC Qt6::Core Qt6::Network PRIVATE taos_client_ffi)
set_target_properties(taosqt PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION 0
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
# Keep the Rust runtime symbols out of the public surface of the .so.
target_link_options(taosqt PRIVATE -Wl,--exclude-libs,ALL)

if(Qt6Qml_FOUND)
  target_sources(taosqt PRIVATE qmlregister.cpp)
  target_link_libraries(taosqt PUBLIC Qt6::Qml)
  target_compile_definitions(taosqt PUBLIC TAOSQT_WITH_QML)
endif()
target_link_libraries(taosqt PUBLIC Qt6::Gui)
