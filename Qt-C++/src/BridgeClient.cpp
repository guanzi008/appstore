#include "BridgeClient.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QTimer>

BridgeClient::BridgeClient(QString repoRoot, QObject *parent)
    : QObject(parent)
    , m_repoRoot(std::move(repoRoot))
{
}

bool BridgeClient::isBusy() const
{
    return m_process != nullptr;
}

void BridgeClient::configureProcess(QProcess *process, const QString &repoRoot, const QString &command)
{
    const QString packagedBackend = QDir::cleanPath(QDir(repoRoot).filePath(QStringLiteral("../../bin/utpublisher-python-backend")));
    const QString devVenvPython = repoRoot + QStringLiteral("/.venv/bin/python");
    const bool usePackagedBackend = QFileInfo::exists(packagedBackend);
    if (usePackagedBackend) {
        process->setProgram(packagedBackend);
        process->setArguments({command});
    } else {
        const QString pythonProgram = QFileInfo::exists(devVenvPython) ? devVenvPython : QStringLiteral("python3");
        process->setProgram(pythonProgram);
        process->setArguments({QStringLiteral("-m"), QStringLiteral("ui.cpp_bridge"), command});
    }
    process->setWorkingDirectory(repoRoot);

    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    const QString oldPythonPath = environment.value(QStringLiteral("PYTHONPATH"));
    const QString bytecodeRoot = QDir::cleanPath(QDir(repoRoot).filePath(QStringLiteral("../../lib/python-bytecode")));
    QStringList pythonPathEntries;
    if (QFileInfo::exists(bytecodeRoot) && QFileInfo(bytecodeRoot).isDir()) {
        pythonPathEntries.append(bytecodeRoot);
    }
    pythonPathEntries.append(repoRoot);
    if (!usePackagedBackend && !oldPythonPath.isEmpty()) {
        pythonPathEntries.append(oldPythonPath);
    }
    environment.insert(QStringLiteral("PYTHONPATH"), pythonPathEntries.join(QStringLiteral(":")));
    environment.insert(QStringLiteral("PYTHONDONTWRITEBYTECODE"), QStringLiteral("1"));
    if (usePackagedBackend) {
        environment.insert(QStringLiteral("PYTHONNOUSERSITE"), QStringLiteral("1"));
        environment.insert(QStringLiteral("UTPUBLISHER_PYTHON_BYTECODE_ROOT"), bytecodeRoot);
        environment.insert(
            QStringLiteral("UTPUBLISHER_PYTHON_RUNTIME_ROOT"),
            QDir::cleanPath(QDir(repoRoot).filePath(QStringLiteral("../../python-runtime")))
        );
    }
    const QString dataRoot = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (!dataRoot.isEmpty()) {
        QDir().mkpath(dataRoot);
        const QDir dataDir(dataRoot);
        environment.insert(QStringLiteral("UTPUBLISHER_CAPABILITY_CACHE_DIR"), dataDir.filePath(QStringLiteral("capabilities")));
        environment.insert(QStringLiteral("UTPUBLISHER_SESSION_CACHE_DIR"), dataDir.filePath(QStringLiteral("session-state")));
        environment.insert(QStringLiteral("UTPUBLISHER_OUTPUT_ROOT"), dataDir.filePath(QStringLiteral("output")));
        environment.insert(QStringLiteral("UTPUBLISHER_PREFERENCES_PATH"), dataDir.filePath(QStringLiteral("preferences.json")));
        environment.insert(QStringLiteral("UTPUBLISHER_WEBENGINE_CACHE_DIR"), dataDir.filePath(QStringLiteral("webengine")));
    }
    process->setProcessEnvironment(environment);
}

void BridgeClient::callAsync(const QString &command, const QJsonObject &payload, int timeoutMs)
{
    if (m_process != nullptr) {
        emit commandFailed(command, tr("另一个后端任务正在执行，请稍后再试。"), {});
        return;
    }

    m_currentCommand = command;
    m_process = new QProcess(this);
    configureProcess(m_process, m_repoRoot, command);

    const QByteArray input = QJsonDocument(payload).toJson(QJsonDocument::Compact);

    connect(m_process, &QProcess::started, this, [this, input]() {
        m_process->write(input);
        m_process->closeWriteChannel();
    });
    connect(m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, &BridgeClient::finishProcess);
    connect(m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            const QString command = m_currentCommand;
            const QString message = tr("无法启动后端进程。");
            cleanupProcess();
            emit commandFailed(command, message, {});
        }
    });

    m_timeout = new QTimer(this);
    m_timeout->setSingleShot(true);
    connect(m_timeout, &QTimer::timeout, this, [this]() {
        if (m_process == nullptr) {
            return;
        }
        const QString command = m_currentCommand;
        m_process->kill();
        cleanupProcess();
        emit commandFailed(command, tr("后端任务超时。"), {});
    });

    emit commandStarted(command);
    m_timeout->start(timeoutMs);
    m_process->start();
}

void BridgeClient::finishProcess(int exitCode, QProcess::ExitStatus exitStatus)
{
    if (m_process == nullptr) {
        return;
    }

    const QString command = m_currentCommand;
    const QByteArray stdoutData = m_process->readAllStandardOutput();
    const QString stderrText = QString::fromUtf8(m_process->readAllStandardError()).trimmed();

    QJsonParseError parseError;
    const QJsonDocument document = parseEnvelopeDocument(stdoutData, &parseError);
    if (exitStatus != QProcess::NormalExit || parseError.error != QJsonParseError::NoError || !document.isObject()) {
        cleanupProcess();
        const QString outputText = QString::fromUtf8(stdoutData).trimmed();
        QString message = tr("后端命令失败：%1").arg(command);
        if (!stderrText.isEmpty()) {
            message += QStringLiteral("\n") + stderrText;
        } else if (!outputText.isEmpty()) {
            message += QStringLiteral("\n") + outputText.left(2000);
        }
        emit commandFailed(command, message, {});
        return;
    }

    const QJsonObject envelope = document.object();
    if (exitCode != 0 && envelope.value(QStringLiteral("ok")).toBool(false)) {
        cleanupProcess();
        QString message = tr("后端命令异常退出：%1").arg(command);
        if (!stderrText.isEmpty()) {
            message += QStringLiteral("\n") + stderrText;
        }
        emit commandFailed(command, message, {});
        return;
    }
    if (!envelope.value(QStringLiteral("ok")).toBool(false)) {
        const QString message = envelope.value(QStringLiteral("error")).toString(tr("后端返回失败。"));
        const QString traceback = envelope.value(QStringLiteral("traceback")).toString();
        cleanupProcess();
        emit commandFailed(command, message, traceback);
        return;
    }

    const QJsonObject data = envelope.value(QStringLiteral("data")).toObject();
    cleanupProcess();
    emit commandFinished(command, data);
}

QJsonDocument BridgeClient::parseEnvelopeDocument(const QByteArray &stdoutData, QJsonParseError *parseError)
{
    QJsonDocument document = QJsonDocument::fromJson(stdoutData, parseError);
    if (parseError->error == QJsonParseError::NoError && document.isObject()) {
        return document;
    }

    const QList<QByteArray> lines = stdoutData.split('\n');
    for (auto it = lines.crbegin(); it != lines.crend(); ++it) {
        const QByteArray line = it->trimmed();
        if (!line.startsWith('{') || !line.endsWith('}')) {
            continue;
        }
        document = QJsonDocument::fromJson(line, parseError);
        if (parseError->error == QJsonParseError::NoError && document.isObject()) {
            return document;
        }
    }

    parseError->error = QJsonParseError::IllegalValue;
    parseError->offset = 0;
    return {};
}

void BridgeClient::cleanupProcess()
{
    if (m_timeout != nullptr) {
        m_timeout->stop();
        m_timeout->deleteLater();
        m_timeout = nullptr;
    }
    if (m_process != nullptr) {
        m_process->deleteLater();
        m_process = nullptr;
    }
    m_currentCommand.clear();
}
