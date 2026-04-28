#include <Python.h>

#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFileInfo>

namespace {

std::wstring toWideString(const QString &value)
{
    return value.toStdWString();
}

bool handlePythonStatus(PyStatus status, PyConfig *config, int *exitCode)
{
    if (!PyStatus_Exception(status)) {
        return true;
    }

    if (PyStatus_IsExit(status)) {
        *exitCode = status.exitcode;
    } else {
        qCritical("failed to initialize embedded Python: %s", status.err_msg ? status.err_msg : "unknown error");
        *exitCode = 120;
    }
    PyConfig_Clear(config);
    return false;
}

bool setConfigString(PyConfig *config, wchar_t **target, const QString &value, int *exitCode)
{
    const std::wstring wideValue = toWideString(value);
    return handlePythonStatus(PyConfig_SetString(config, target, wideValue.c_str()), config, exitCode);
}

bool appendModulePath(PyConfig *config, const QString &value, int *exitCode)
{
    const std::wstring wideValue = toWideString(value);
    return handlePythonStatus(PyWideStringList_Append(&config->module_search_paths, wideValue.c_str()), config, exitCode);
}

int callBridgeMain()
{
    PyObject *module = PyImport_ImportModule("ui.cpp_bridge");
    if (module == nullptr) {
        PyErr_Print();
        return 1;
    }

    PyObject *mainFunction = PyObject_GetAttrString(module, "main");
    if (mainFunction == nullptr || !PyCallable_Check(mainFunction)) {
        Py_XDECREF(mainFunction);
        Py_DECREF(module);
        qCritical("embedded Python backend is missing ui.cpp_bridge.main");
        return 1;
    }

    PyObject *result = PyObject_CallNoArgs(mainFunction);
    Py_DECREF(mainFunction);
    Py_DECREF(module);
    if (result == nullptr) {
        PyErr_Print();
        return 1;
    }

    const long exitCode = PyLong_AsLong(result);
    Py_DECREF(result);
    if (PyErr_Occurred()) {
        PyErr_Print();
        return 1;
    }
    return static_cast<int>(exitCode);
}

} // namespace

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);

    const QFileInfo executableInfo(QCoreApplication::applicationFilePath());
    const QDir filesDir(executableInfo.dir().absoluteFilePath(QStringLiteral("..")));
    const QString bytecodeRoot = filesDir.filePath(QStringLiteral("lib/python-bytecode"));
    const QString runtimeRoot = filesDir.filePath(QStringLiteral("share/" APPSTORE_APPID));
    const QString pythonRuntimeRoot = filesDir.filePath(QStringLiteral("python-runtime"));
    const QString pythonLibRoot = filesDir.filePath(QStringLiteral("python-runtime/lib"));
    const QString pythonVersionDir = QStringLiteral("python%1.%2").arg(PY_MAJOR_VERSION).arg(PY_MINOR_VERSION);
    const QString stdlibRoot = QDir(pythonLibRoot).filePath(pythonVersionDir);
    const QString dynloadRoot = QDir(stdlibRoot).filePath(QStringLiteral("lib-dynload"));
    const QString sitePackagesRoot = QDir(stdlibRoot).filePath(QStringLiteral("site-packages"));

    if (!QFileInfo::exists(QDir(stdlibRoot).filePath(QStringLiteral("encodings")))) {
        qCritical("embedded Python runtime is incomplete: %s", qPrintable(stdlibRoot));
        return 127;
    }
    if (!QFileInfo::exists(bytecodeRoot)) {
        qCritical("Python bytecode runtime is missing: %s", qPrintable(bytecodeRoot));
        return 127;
    }

    qputenv("PYTHONDONTWRITEBYTECODE", "1");
    qputenv("PYTHONNOUSERSITE", "1");
    qputenv("UTPUBLISHER_PYTHON_RUNTIME_ROOT", pythonRuntimeRoot.toLocal8Bit());
    qputenv("UTPUBLISHER_PYTHON_BYTECODE_ROOT", bytecodeRoot.toLocal8Bit());
    QDir::setCurrent(runtimeRoot);

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.isolated = 1;
    config.use_environment = 0;
    config.user_site_directory = 0;
    config.site_import = 0;
    config.write_bytecode = 0;
    config.parse_argv = 0;
    config.module_search_paths_set = 1;

    int exitCode = 0;
    if (!setConfigString(&config, &config.program_name, QCoreApplication::applicationFilePath(), &exitCode)
        || !setConfigString(&config, &config.executable, QCoreApplication::applicationFilePath(), &exitCode)
        || !setConfigString(&config, &config.home, pythonRuntimeRoot, &exitCode)
        || !handlePythonStatus(PyConfig_SetBytesArgv(&config, argc, argv), &config, &exitCode)
        || !appendModulePath(&config, bytecodeRoot, &exitCode)
        || !appendModulePath(&config, runtimeRoot, &exitCode)
        || !appendModulePath(&config, stdlibRoot, &exitCode)
        || !appendModulePath(&config, dynloadRoot, &exitCode)
        || !appendModulePath(&config, sitePackagesRoot, &exitCode)) {
        return exitCode;
    }

    if (!handlePythonStatus(Py_InitializeFromConfig(&config), &config, &exitCode)) {
        return exitCode;
    }
    PyConfig_Clear(&config);

    exitCode = callBridgeMain();
    if (Py_FinalizeEx() < 0 && exitCode == 0) {
        return 120;
    }
    return exitCode;
}
