#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QProcessEnvironment>

namespace {

QString appendPythonPath(const QString &first, const QString &second, const QString &existing)
{
    QStringList entries;
    if (!first.isEmpty()) {
        entries.append(first);
    }
    if (!second.isEmpty()) {
        entries.append(second);
    }
    if (!existing.isEmpty()) {
        entries.append(existing);
    }
    return entries.join(QStringLiteral(":"));
}

} // namespace

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);

    const QFileInfo executableInfo(QCoreApplication::applicationFilePath());
    const QDir filesDir(executableInfo.dir().absoluteFilePath(QStringLiteral("..")));
    const QString venvPython = filesDir.filePath(QStringLiteral("venv/bin/python"));
    const QString pythonProgram = QFileInfo::exists(venvPython) ? venvPython : QStringLiteral("python3");
    const QString bytecodeRoot = filesDir.filePath(QStringLiteral("lib/python-bytecode"));
    const QString runtimeRoot = filesDir.filePath(QStringLiteral("share/" APPSTORE_APPID));

    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(
        QStringLiteral("PYTHONPATH"),
        appendPythonPath(bytecodeRoot, runtimeRoot, environment.value(QStringLiteral("PYTHONPATH")))
    );
    environment.insert(QStringLiteral("PYTHONDONTWRITEBYTECODE"), QStringLiteral("1"));
    environment.insert(QStringLiteral("PYTHONNOUSERSITE"), QStringLiteral("1"));

    QStringList arguments;
    arguments << QStringLiteral("-m") << QStringLiteral("ui.cpp_bridge");
    for (int index = 1; index < argc; ++index) {
        arguments << QString::fromLocal8Bit(argv[index]);
    }

    QProcess process;
    process.setProgram(pythonProgram);
    process.setArguments(arguments);
    process.setProcessEnvironment(environment);
    process.setWorkingDirectory(runtimeRoot);
    process.setInputChannelMode(QProcess::ForwardedInputChannel);
    process.setProcessChannelMode(QProcess::ForwardedChannels);
    process.start();
    if (!process.waitForStarted()) {
        qCritical("failed to start Python backend: %s", qPrintable(process.errorString()));
        return 127;
    }
    process.waitForFinished(-1);
    if (process.exitStatus() != QProcess::NormalExit) {
        return 128;
    }
    return process.exitCode();
}
