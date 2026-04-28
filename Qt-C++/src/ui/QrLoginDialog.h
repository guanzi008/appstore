#pragma once

#include <QDialog>
#include <QJsonObject>
#include <QProcess>

class QLabel;
class QPushButton;
class QTimer;

class QrLoginDialog final : public QDialog
{
    Q_OBJECT
    Q_DISABLE_COPY(QrLoginDialog)

public:
    explicit QrLoginDialog(QString repoRoot, QString accountLabel, QWidget *parent = nullptr);
    ~QrLoginDialog() override;

    [[nodiscard]] QJsonObject resultData() const;

public slots:
    void reject() override;

private:
    void buildUi();
    void startLogin();
    void cleanupProcess();
    void cancelProcess();
    void handleReadyRead();
    void handleFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void handleProcessError(QProcess::ProcessError error);
    void handleTimeout();
    void handleJsonLine(const QByteArray &line);
    void handleEvent(const QJsonObject &event);
    void handleEnvelope(const QJsonObject &envelope);
    void setQrImage(const QString &imageBase64);
    void showFailure(const QString &message);

    QString m_repoRoot;
    QString m_accountLabel;
    QJsonObject m_resultData;
    QProcess *m_process = nullptr;
    QTimer *m_timeout = nullptr;
    QLabel *m_qrLabel = nullptr;
    QLabel *m_statusLabel = nullptr;
    QPushButton *m_refreshButton = nullptr;
    QPushButton *m_cancelButton = nullptr;
    QByteArray m_stdoutBuffer;
    QByteArray m_stderrBuffer;
    bool m_completed = false;
    bool m_cancelRequested = false;
};
