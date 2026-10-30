# tabletennis - ReXGlue Recompiled Project (Windows + Android ARM64)
#
# Android build layout follows the MCLA Android port: the SDK is built in-tree
# (REXSDK_DIR), the game is libmain.so loaded by SDL3's Android activity, and
# librexruntime gets the adrenotools custom-driver Vulkan loader hook.

cmake_minimum_required(VERSION 3.25)
project(tabletennis LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(ANDROID)
    add_compile_options(-march=armv8.2-a -fno-emulated-tls)
endif()

if(ANDROID AND CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
    add_compile_options("$<$<COMPILE_LANGUAGE:CXX>:-O3>")
endif()

if(ANDROID)
    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libadrenotools/CMakeLists.txt")
        message(FATAL_ERROR "third_party/libadrenotools is missing (setup script clones it).")
    endif()
    add_subdirectory(third_party/libadrenotools)
    set_target_properties(adrenotools linkernsbypass PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()

include(cmake/rexglue.cmake)

if(ANDROID)
    include_directories("${REXSDK_DIR}/thirdparty/libucontext/include"
                        "${REXSDK_DIR}/thirdparty/imgui"
                        "${REXSDK_DIR}/thirdparty/libucontext/arch/aarch64/include")
    target_sources(rexruntime PRIVATE src/android_gpu_driver.cpp)
    target_include_directories(rexruntime PRIVATE third_party/libadrenotools/include)
    target_link_libraries(rexruntime PRIVATE adrenotools)
endif()

set(TABLETENNIS_SOURCES
    src/main.cpp
    src/intro_skip.cpp
    src/tt_profiler.cpp
)

if(ANDROID)
    add_library(tabletennis SHARED ${TABLETENNIS_SOURCES})
    set_target_properties(tabletennis PROPERTIES OUTPUT_NAME main)
    target_compile_options(tabletennis PRIVATE -include SDL3/SDL_main.h)
elseif(WIN32)
    add_executable(tabletennis WIN32 ${TABLETENNIS_SOURCES})
else()
    add_executable(tabletennis ${TABLETENNIS_SOURCES})
endif()

rexglue_setup_target(tabletennis)

if(ANDROID)
    # Calls between recompiled functions inside libmain.so bind locally instead of
    # going through the PLT (same trick as the GTA IV Android port).
    target_link_options(tabletennis PRIVATE "-Wl,-Bsymbolic-functions")
endif()

if(ANDROID AND TARGET tabletennis_recomp)
    set_target_properties(tabletennis_recomp PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()

# Swap the SDK's directory-only ReXApp for the ISO-capable adapter.
# On Android the adapter also carries the MCLA patch's Android log-path changes.
get_target_property(TABLETENNIS_HOST_SOURCES tabletennis SOURCES)
set(TABLETENNIS_SDK_APP "${REXGLUE_SHARE_DIR}/rex_app.cpp")
if(NOT TABLETENNIS_SDK_APP IN_LIST TABLETENNIS_HOST_SOURCES)
    message(FATAL_ERROR "SDK application source not found; cannot enable ISO support")
endif()
list(REMOVE_ITEM TABLETENNIS_HOST_SOURCES "${TABLETENNIS_SDK_APP}")
set_property(TARGET tabletennis PROPERTY SOURCES "${TABLETENNIS_HOST_SOURCES}")
if(ANDROID)
    target_sources(tabletennis PRIVATE sdk-overrides/rex_app_android.cpp)
else()
    target_sources(tabletennis PRIVATE sdk-overrides/rex_app.cpp)
endif()

target_include_directories(tabletennis BEFORE PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/sdk-overrides")
if(TARGET tabletennis_recomp)
    target_include_directories(tabletennis_recomp BEFORE PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/sdk-overrides")
endif()
