#include "ui/QrLoginDialog.h"

#include "BridgeClient.h"

#include <QByteArray>
#include <QDialogButtonBox>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QLabel>
#include <QMessageBox>
#include <QPixmap>
#include <QPushButton>
#include <QTimer>
#include <QVBoxLayout>

#include <utility>

QrLoginDialog::QrLoginDialog(QString repoRoot, QString accountLabel, QWidget *parent)
    : QDialog(parent)
    , m_repoRoot(std::move(repoRoot))
    , m_accountLabel(std::move(accountLabel))
{
    setWindowTitle(QStringLiteral("微信扫码登录"));
    setModal(true);
    setMinimumSize(420, 520);
    buildUi();
    startLogin();
}

QrLoginDialog::~QrLoginDialog()
{
    cancelProcess();
}

QJsonObject QrLoginDialog::resultData() const
{
    return m_resultData;
}

void QrLoginDialog::reject()
{
    m_cancelRequested = true;
    cancelProcess();
    QDialog::reject();
}

void QrLoginDialog::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(24, 20, 24, 20);
    layout->setSpacing(12);

    auto *title = new QLabel(QStringLiteral("请使用微信扫码登录统信账号"), this);
    title->setObjectName(QStringLiteral("DialogTitle"));
    title->setAlignment(Qt::AlignCenter);
    layout->addWidget(title);

    auto *hint = new QLabel(QStringLiteral("二维码来自真实网页登录链路，扫码确认后自动保存 Cookie 会话。"), this);
    hint->setObjectName(QStringLiteral("MutedText"));
    hint->setAlignment(Qt::AlignCenter);
    hint->setWordWrap(true);
    layout->addWidget(hint);

    m_qrLabel = new QLabel(QStringLiteral("正在加载二维码..."), this);
    m_qrLabel->setAlignment(Qt::AlignCenter);
    m_qrLabel->setMinimumSize(300, 300);
    m_qrLabel->setStyleSheet(QStringLiteral(
        "QLabel { border: 1px solid rgba(120, 140, 160, 95); border-radius: 10px; background: rgba(255,255,255,115); }"));
    layout->addWidget(m_qrLabel, 1);

    m_statusLabel = new QLabel(QStringLiteral("准备中。"), this);
    m_statusLabel->setObjectName(QStringLiteral("MutedText"));
    m_statusLabel->setAlignment(Qt::AlignCenter);
    m_statusLabel->setWordWrap(true);
    layout->addWidget(m_statusLabel);

    auto *buttons = new QDialogButtonBox(this);
    m_refreshButton = buttons->addButton(QStringLiteral("刷新二维码"), QDialogButtonBox::ActionRole);
    m_cancelButton = buttons->addButton(QDialogButtonBox::Cancel);
    m_refreshButton->setCursor(Qt::PointingHandCursor);
    m_cancelButton->setCursor(Qt::PointingHandCursor);
    connect(m_refreshButton, &QPushButton::clicked, this, [this]() {
        cancelProcess();
        startLogin();
    });
    connect(m_cancelButton, &QPushButton::clicked, this, &QrLoginDialog::reject);
    layout->addWidget(buttons);
}

void QrLoginDialog::startLogin()
{
    cleanupProcess();
    m_completed = false;
    m_cancelRequested = false;
    m_stdoutBuffer.clear();
    m_stderrBuffer.clear();
    m_qrLabel->setPixmap(QPixmap());
    m_qrLabel->setText(QStringLiteral("正在加载二维码..."));
    m_statusLabel->setText(QStringLiteral("正在获取微信登录二维码。"));

    m_process = new QProcess(this);
    BridgeClient::configureProcess(m_process, m_repoRoot, QStringLiteral("login_wechat_qr_stream"));

    const QJsonObject payload{
        {QStringLiteral("account_label"), m_accountLabel.isEmpty() ? QStringLiteral("wechat-login") : m_accountLabel},
    };
    const QByteArray input = QJsonDocument(payload).toJson(QJsonDocument::Compact);

    connect(m_process, &QProcess::started, this, [this, input]() {
        m_process->write(input);
        m_process->closeWriteChannel();
    });
    connect(m_process, &QProcess::readyReadStandardOutput, this, &QrLoginDialog::handleReadyRead);
    connect(m_process, &QProcess::readyReadStandardError, this, [this]() {
        if (m_process != nullptr) {
            m_stderrBuffer.append(m_process->readAllStandardError());
        }
    });
    connect(m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, &QrLoginDialog::handleFinished);
    connect(m_process, &QProcess::errorOccurred, this, &QrLoginDialog::handleProcessError);

    m_timeout = new QTimer(this);
    m_timeout->setSingleShot(true);
    connect(m_timeout, &QTimer::timeout, this, &QrLoginDialog::handleTimeout);
    m_timeout->start(600000);

    m_process->start();
}

void QrLoginDialog::cleanupProcess()
{
    if (m_timeout != nullptr) {
        m_timeout->stop();
        m_timeout->deleteLater();
        m_timeout = nullptr;
    }
    if (m_process != nullptr) {
        m_process->disconnect(this);
        m_process->deleteLater();
        m_process = nullptr;
    }
    m_stdoutBuffer.clear();
    m_stderrBuffer.clear();
}

void QrLoginDialog::cancelProcess()
{
    if (m_process != nullptr && m_process->state() != QProcess::NotRunning) {
        m_process->kill();
        m_process->waitForFinished(1000);
    }
    cleanupProcess();
}

void QrLoginDialog::handleReadyRead()
{
    if (m_process == nullptr) {
        return;
    }
    m_stdoutBuffer.append(m_process->readAllStandardOutput());
    while (true) {
        const int newlineIndex = m_stdoutBuffer.indexOf('\n');
        if (newlineIndex < 0) {
            break;
        }
        const QByteArray line = m_stdoutBuffer.left(newlineIndex).trimmed();
        m_stdoutBuffer.remove(0, newlineIndex + 1);
        handleJsonLine(line);
    }
}

void QrLoginDialog::handleFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    if (m_process == nullptr) {
        return;
    }

    handleReadyRead();
    const QByteArray trailingLine = m_stdoutBuffer.trimmed();
    if (!trailingLine.isEmpty()) {
        handleJsonLine(trailingLine);
    }
    if (m_process == nullptr) {
        return;
    }

    if (m_completed || m_cancelRequested) {
        cleanupProcess();
        return;
    }

    QString message = QStringLiteral("扫码登录后端异常退出。");
    m_stderrBuffer.append(m_process->readAllStandardError());
    const QString stderrText = QString::fromUtf8(m_stderrBuffer).trimmed();
    if (!stderrText.isEmpty()) {
        message = stderrText.left(1200);
    } else if (exitStatus != QProcess::NormalExit || exitCode != 0) {
        message = QStringLiteral("扫码登录后端退出码：%1").arg(exitCode);
    }
    cleanupProcess();
    showFailure(message);
}

void QrLoginDialog::handleProcessError(QProcess::ProcessError error)
{
    if (error != QProcess::FailedToStart || m_process == nullptr) {
        return;
    }
    cleanupProcess();
    showFailure(QStringLiteral("无法启动扫码登录后端进程。"));
}

void QrLoginDialog::handleTimeout()
{
    cancelProcess();
    showFailure(QStringLiteral("微信扫码登录超时。"));
}

void QrLoginDialog::handleJsonLine(const QByteArray &line)
{
    if (line.isEmpty() || !line.startsWith('{') || !line.endsWith('}')) {
        return;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        return;
    }
    const QJsonObject object = document.object();
    if (object.contains(QStringLiteral("event"))) {
        handleEvent(object);
        return;
    }
    if (object.contains(QStringLiteral("ok"))) {
        handleEnvelope(object);
    }
}

void QrLoginDialog::handleEvent(const QJsonObject &event)
{
    const QString type = event.value(QStringLiteral("event")).toString();
    if (type == QStringLiteral("status")) {
        const QString message = event.value(QStringLiteral("message")).toString().trimmed();
        m_statusLabel->setText(message.isEmpty() ? QStringLiteral("等待扫码确认。") : message);
        return;
    }
    if (type == QStringLiteral("qr")) {
        setQrImage(event.value(QStringLiteral("image_base64")).toString());
    }
}

void QrLoginDialog::handleEnvelope(const QJsonObject &envelope)
{
    if (!envelope.value(QStringLiteral("ok")).toBool(false)) {
        const QString message = envelope.value(QStringLiteral("error")).toString(QStringLiteral("扫码登录失败。"));
        cleanupProcess();
        showFailure(message);
        return;
    }

    m_resultData = envelope.value(QStringLiteral("data")).toObject();
    m_completed = true;
    cleanupProcess();
    accept();
}

void QrLoginDialog::setQrImage(const QString &imageBase64)
{
    const QByteArray imageBytes = QByteArray::fromBase64(imageBase64.toUtf8());
    QPixmap pixmap;
    if (!pixmap.loadFromData(imageBytes)) {
        m_qrLabel->setPixmap(QPixmap());
        m_qrLabel->setText(QStringLiteral("二维码加载失败"));
        m_statusLabel->setText(QStringLiteral("二维码图片解析失败，请刷新重试。"));
        return;
    }
    m_qrLabel->setText(QString());
    m_qrLabel->setPixmap(pixmap.scaled(m_qrLabel->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
}

void QrLoginDialog::showFailure(const QString &message)
{
    if (m_cancelRequested) {
        return;
    }
    m_qrLabel->setPixmap(QPixmap());
    m_qrLabel->setText(QStringLiteral("扫码登录失败"));
    m_statusLabel->setText(message);
    QMessageBox::warning(this, QStringLiteral("扫码登录失败"), message);
}
