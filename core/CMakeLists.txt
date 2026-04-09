cmake_minimum_required(VERSION 3.20)
project(RegBrokerCore VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Release by default for performance
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

set(SOURCES
    src/main.cpp
    src/core/hive.cpp
    src/core/decoder.cpp
    src/core/recovery.cpp
)

add_executable(regbroker-core ${SOURCES})

target_include_directories(regbroker-core PRIVATE include)

if(MSVC)
    target_compile_options(regbroker-core PRIVATE /W4 /utf-8)
    # Static runtime for portable binary
    set_property(TARGET regbroker-core PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    target_compile_options(regbroker-core PRIVATE -Wall -Wextra)
endif()

# Install to project bin/
install(TARGETS regbroker-core DESTINATION ${CMAKE_SOURCE_DIR}/../bin)
