cmake_minimum_required(VERSION 3.21)
project(taos-poc-qt LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt6 6.8 REQUIRED COMPONENTS Gui Qml Quick)
find_package(LayerShellQt REQUIRED)
qt_standard_project_setup(REQUIRES 6.8)

set_source_files_properties(qml/Theme.qml PROPERTIES QT_QML_SINGLETON_TYPE TRUE)

qt_add_executable(taos-poc-qt src/main.cpp)
qt_add_qml_module(taos-poc-qt
    URI Poc
    VERSION 1.0
    QML_FILES qml/Main.qml qml/Bar.qml qml/Theme.qml
    SOURCES src/pocconfig.h src/pocconfig.cpp
)
target_link_libraries(taos-poc-qt PRIVATE Qt6::Gui Qt6::Qml Qt6::Quick LayerShellQt::Interface)
target_compile_options(taos-poc-qt PRIVATE -Wall -Wextra)
target_include_directories(taos-poc-qt PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
