cmake_minimum_required(VERSION 3.21)

project(UTPublisherQtCpp LANGUAGES CXX)

set(APPSTORE_APPID "com.uniontech.utpublisher" CACHE STRING "deepin/UOS store appid")
set(APPSTORE_APP_ROOT "opt/apps/${APPSTORE_APPID}" CACHE STRING "deepin/UOS store install root")
set(APPSTORE_RUNTIME_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." CACHE PATH "Runtime root used by the Python backend")
set(APPSTORE_INFO_VERSION "0.1.0.1" CACHE STRING "Version written to /opt/apps/<appid>/info")
set(APPSTORE_INFO_ARCH "${CMAKE_SYSTEM_PROCESSOR}" CACHE STRING "Architecture written to /opt/apps/<appid>/info")
set(APPSTORE_PROJECT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/..")
set(APPSTORE_RUNTIME_SHARE_DIR "${APPSTORE_APP_ROOT}/files/share/${APPSTORE_APPID}")
set(APPSTORE_PYTHON_RUNTIME_DIR "${CMAKE_CURRENT_BINARY_DIR}/python-runtime")
set(APPSTORE_BUNDLED_FONTS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/resources/fonts")
set(APPSTORE_VENDORED_PYTHON_REQUIREMENTS
    "${APPSTORE_PROJECT_ROOT}/appstore/requirements.txt"
    CACHE STRING "Semicolon-separated requirements files installed into the embedded Python runtime"
)
set(APPSTORE_PYTHON_WHEELHOUSE
    "${CMAKE_CURRENT_SOURCE_DIR}/packaging/python-wheelhouse"
    CACHE PATH "Offline wheelhouse used to vendor Python requirements into the embedded runtime"
)
option(APPSTORE_EMBED_PYTHON_RUNTIME "Build and install a bundled embedded Python runtime" ON)
option(APPSTORE_VENDOR_PYTHON_DEPS "Install Python requirements into the bundled embedded Python runtime" OFF)
option(APPSTORE_INSTALL_PYTHON_SOURCES "Install Python source trees next to the bytecode runtime" OFF)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC OFF)
set(CMAKE_AUTORCC OFF)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Embed)
find_package(PkgConfig REQUIRED)
pkg_check_modules(DTK6WIDGET REQUIRED IMPORTED_TARGET dtk6widget)

qt_add_executable(utpublisher-qt-cpp
    src/main.cpp
    src/BridgeClient.cpp
    src/MainWindow.cpp
    src/core/AppJson.cpp
    src/ui/AppTheme.cpp
    src/ui/LoginDialog.cpp
    src/ui/MetadataPanel.cpp
    src/ui/QrLoginDialog.cpp
    src/ui/ScreenshotPanel.cpp
    src/ui/SidebarWidget.cpp
    src/ui/TargetMatrixPanel.cpp
    src/ui/WorkflowBar.cpp
)

qt_add_executable(utpublisher-python-backend
    src/backend_launcher.cpp
)

target_include_directories(utpublisher-qt-cpp PRIVATE src)
target_compile_definitions(utpublisher-qt-cpp PRIVATE
    APPSTORE_REPO_ROOT="${APPSTORE_RUNTIME_REPO_ROOT}"
    APPSTORE_BUNDLED_FONTS_DIR="${APPSTORE_BUNDLED_FONTS_DIR}"
    APPSTORE_INSTALLED_FONTS_DIR="/${APPSTORE_RUNTIME_SHARE_DIR}/fonts"
)
target_link_libraries(utpublisher-qt-cpp PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets PkgConfig::DTK6WIDGET)

target_compile_definitions(utpublisher-python-backend PRIVATE
    APPSTORE_APPID="${APPSTORE_APPID}"
)
target_link_libraries(utpublisher-python-backend PRIVATE Qt6::Core Python3::Python)
set_target_properties(utpublisher-python-backend PROPERTIES
    BUILD_RPATH "${APPSTORE_PYTHON_RUNTIME_DIR}/lib"
    INSTALL_RPATH "$ORIGIN/../python-runtime/lib"
)

file(GLOB_RECURSE APPSTORE_PYTHON_SOURCES CONFIGURE_DEPENDS
    "${APPSTORE_PROJECT_ROOT}/appstore/*.py"
    "${APPSTORE_PROJECT_ROOT}/ui/*.py"
    "${APPSTORE_PROJECT_ROOT}/openGA/*.py"
)
set(APPSTORE_PYTHON_BYTECODE_DIR "${CMAKE_CURRENT_BINARY_DIR}/python-bytecode")
file(GLOB APPSTORE_PYTHON_WHEELHOUSE_FILES CONFIGURE_DEPENDS
    "${APPSTORE_PYTHON_WHEELHOUSE}/*"
)
add_custom_command(
    OUTPUT "${APPSTORE_PYTHON_BYTECODE_DIR}/.stamp"
    COMMAND "${CMAKE_COMMAND}" -E rm -rf "${APPSTORE_PYTHON_BYTECODE_DIR}"
    COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/packaging/compile_python_bytecode.py"
        "${APPSTORE_PROJECT_ROOT}"
        "${APPSTORE_PYTHON_BYTECODE_DIR}"
        appstore ui openGA
    COMMAND "${CMAKE_COMMAND}" -E touch "${APPSTORE_PYTHON_BYTECODE_DIR}/.stamp"
    DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/packaging/compile_python_bytecode.py"
        ${APPSTORE_PYTHON_SOURCES}
    COMMENT "Compiling Python backend to bytecode"
    VERBATIM
)
add_custom_target(appstore-python-bytecode ALL
    DEPENDS "${APPSTORE_PYTHON_BYTECODE_DIR}/.stamp"
)

install(TARGETS utpublisher-qt-cpp
    RUNTIME DESTINATION "${APPSTORE_APP_ROOT}/files/bin"
)
install(TARGETS utpublisher-python-backend
    RUNTIME DESTINATION "${APPSTORE_APP_ROOT}/files/bin"
)
install(DIRECTORY "${APPSTORE_PYTHON_BYTECODE_DIR}/"
    DESTINATION "${APPSTORE_APP_ROOT}/files/lib/python-bytecode"
    PATTERN ".stamp" EXCLUDE
)

if(APPSTORE_EMBED_PYTHON_RUNTIME)
    set(APPSTORE_PYTHON_RUNTIME_ARGS
        "${CMAKE_CURRENT_SOURCE_DIR}/packaging/build_python_runtime.py"
        "${APPSTORE_PYTHON_RUNTIME_DIR}"
    )
    if(APPSTORE_VENDOR_PYTHON_DEPS)
        list(APPEND APPSTORE_PYTHON_RUNTIME_ARGS "--install-deps")
        if(APPSTORE_PYTHON_WHEELHOUSE AND EXISTS "${APPSTORE_PYTHON_WHEELHOUSE}")
            list(APPEND APPSTORE_PYTHON_RUNTIME_ARGS "--wheelhouse" "${APPSTORE_PYTHON_WHEELHOUSE}")
        endif()
        foreach(requirements_file ${APPSTORE_VENDORED_PYTHON_REQUIREMENTS})
            if(EXISTS "${requirements_file}")
                list(APPEND APPSTORE_PYTHON_RUNTIME_ARGS "--requirements" "${requirements_file}")
            endif()
        endforeach()
    endif()

    add_custom_command(
        OUTPUT "${APPSTORE_PYTHON_RUNTIME_DIR}/.stamp"
        COMMAND "${CMAKE_COMMAND}" -E rm -rf "${APPSTORE_PYTHON_RUNTIME_DIR}"
        COMMAND "${Python3_EXECUTABLE}" ${APPSTORE_PYTHON_RUNTIME_ARGS}
        COMMAND "${CMAKE_COMMAND}" -E touch "${APPSTORE_PYTHON_RUNTIME_DIR}/.stamp"
        DEPENDS
            "${CMAKE_CURRENT_SOURCE_DIR}/packaging/build_python_runtime.py"
            ${APPSTORE_VENDORED_PYTHON_REQUIREMENTS}
            ${APPSTORE_PYTHON_WHEELHOUSE_FILES}
        COMMENT "Building embedded Python runtime"
        VERBATIM
    )
    add_custom_target(appstore-python-runtime ALL
        DEPENDS "${APPSTORE_PYTHON_RUNTIME_DIR}/.stamp"
    )
    add_dependencies(utpublisher-python-backend appstore-python-runtime)
    install(DIRECTORY "${APPSTORE_PYTHON_RUNTIME_DIR}/"
        DESTINATION "${APPSTORE_APP_ROOT}/files/python-runtime"
        USE_SOURCE_PERMISSIONS
        PATTERN ".stamp" EXCLUDE
    )
endif()

install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/packaging/${APPSTORE_APPID}.desktop"
    DESTINATION "${APPSTORE_APP_ROOT}/entries/applications"
)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/packaging/${APPSTORE_APPID}.svg"
    DESTINATION "${APPSTORE_APP_ROOT}/entries/icons/hicolor/scalable/apps"
)
install(DIRECTORY "${APPSTORE_BUNDLED_FONTS_DIR}/"
    DESTINATION "${APPSTORE_RUNTIME_SHARE_DIR}/fonts"
    FILES_MATCHING
    PATTERN "*.ttf"
    PATTERN "*.otf"
    PATTERN "*.ttc"
    PATTERN "*.copyright"
)

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/packaging/info.json.in"
    "${CMAKE_CURRENT_BINARY_DIR}/${APPSTORE_APPID}.info"
    @ONLY
)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/${APPSTORE_APPID}.info"
    DESTINATION "${APPSTORE_APP_ROOT}"
    RENAME info
)

function(install_python_runtime_tree tree_name)
    if(EXISTS "${APPSTORE_PROJECT_ROOT}/${tree_name}")
        install(DIRECTORY "${APPSTORE_PROJECT_ROOT}/${tree_name}"
            DESTINATION "${APPSTORE_RUNTIME_SHARE_DIR}"
            PATTERN "__pycache__" EXCLUDE
            PATTERN "*.pyc" EXCLUDE
            PATTERN "tests" EXCLUDE
            PATTERN "cache" EXCLUDE
            PATTERN "output" EXCLUDE
            PATTERN "examples" EXCLUDE
        )
    endif()
endfunction()

if(APPSTORE_INSTALL_PYTHON_SOURCES)
    install_python_runtime_tree("appstore")
    install_python_runtime_tree("ui")
    install_python_runtime_tree("openGA")
endif()

foreach(requirements_file
        "${APPSTORE_PROJECT_ROOT}/requirements.txt"
        "${APPSTORE_PROJECT_ROOT}/appstore/requirements.txt"
        "${APPSTORE_PROJECT_ROOT}/ui/requirements.txt")
    if(EXISTS "${requirements_file}")
        file(RELATIVE_PATH relative_requirements_path "${APPSTORE_PROJECT_ROOT}" "${requirements_file}")
        get_filename_component(relative_requirements_dir "${relative_requirements_path}" DIRECTORY)
        install(FILES "${requirements_file}"
            DESTINATION "${APPSTORE_RUNTIME_SHARE_DIR}/${relative_requirements_dir}"
        )
    endif()
endforeach()
