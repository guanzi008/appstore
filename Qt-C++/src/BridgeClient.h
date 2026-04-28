#pragma once

#include <QObject>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QProcess>

class QTimer;

class BridgeClient final : public QObject
{
    Q_OBJECT
    Q_DISABLE_COPY(BridgeClient)

public:
    explicit BridgeClient(QString repoRoot, QObject *parent = nullptr);

    [[nodiscard]] bool isBusy() const;
    void callAsync(const QString &command, const QJsonObject &payload, int timeoutMs = 600000);
    static void configureProcess(QProcess *process, const QString &repoRoot, const QString &command);
    static QJsonDocument parseEnvelopeDocument(const QByteArray &stdoutData, QJsonParseError *parseError);

signals:
    void commandStarted(const QString &command);
    void commandFinished(const QString &command, const QJsonObject &data);
    void commandFailed(const QString &command, const QString &message, const QString &traceback);

private:
    void finishProcess(int exitCode, QProcess::ExitStatus exitStatus);
    void cleanupProcess();
    QString m_repoRoot;
    QString m_currentCommand;
    QProcess *m_process = nullptr;
    QTimer *m_timeout = nullptr;
};
