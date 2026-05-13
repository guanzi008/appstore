#include "ui/LoginDialog.h"

#include <QDialogButtonBox>
#include <QGridLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QVBoxLayout>

LoginDialog::LoginDialog(QWidget *parent)
    : QDialog(parent)
{
    setWindowTitle(QStringLiteral("登录统信应用投递助手"));
    setModal(true);
    buildUi();
}

LoginDialog::Mode LoginDialog::mode() const
{
    return m_mode;
}

QString LoginDialog::username() const
{
    return m_usernameEdit->text().trimmed();
}

QString LoginDialog::password() const
{
    return m_passwordEdit->text();
}

void LoginDialog::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(24, 20, 24, 20);
    layout->setSpacing(16);

    auto *title = new QLabel(QStringLiteral("登录应用商店账号"), this);
    title->setObjectName(QStringLiteral("DialogTitle"));
    layout->addWidget(title);

    auto *form = new QGridLayout();
    form->setHorizontalSpacing(12);
    form->setVerticalSpacing(12);
    auto *usernameLabel = new QLabel(QStringLiteral("账号"), this);
    auto *passwordLabel = new QLabel(QStringLiteral("密码"), this);
    usernameLabel->setObjectName(QStringLiteral("FieldCaption"));
    passwordLabel->setObjectName(QStringLiteral("FieldCaption"));
    m_usernameEdit = new QLineEdit(this);
    m_usernameEdit->setPlaceholderText(QStringLiteral("请输入账号"));
    m_passwordEdit = new QLineEdit(this);
    m_passwordEdit->setPlaceholderText(QStringLiteral("请输入密码"));
    m_passwordEdit->setEchoMode(QLineEdit::Password);
    form->addWidget(usernameLabel, 0, 0);
    form->addWidget(m_usernameEdit, 0, 1);
    form->addWidget(passwordLabel, 1, 0);
    form->addWidget(m_passwordEdit, 1, 1);
    layout->addLayout(form);

    auto *hint = new QLabel(QStringLiteral("也可以使用扫码登录，扫码完成后会复用后端保存的 Cookie 会话。"), this);
    hint->setObjectName(QStringLiteral("MutedText"));
    hint->setWordWrap(true);
    layout->addWidget(hint);

    auto *buttons = new QDialogButtonBox(this);
    auto *credentialButton = buttons->addButton(QStringLiteral("账号登录"), QDialogButtonBox::AcceptRole);
    auto *qrButton = buttons->addButton(QStringLiteral("📱 扫码登录"), QDialogButtonBox::ActionRole);
    auto *cancelButton = buttons->addButton(QDialogButtonBox::Cancel);
    credentialButton->setCursor(Qt::PointingHandCursor);
    qrButton->setCursor(Qt::PointingHandCursor);
    cancelButton->setCursor(Qt::PointingHandCursor);

    connect(credentialButton, &QPushButton::clicked, this, [this]() {
        if (username().isEmpty() || password().isEmpty()) {
            QMessageBox::information(this, QStringLiteral("信息不完整"), QStringLiteral("请输入账号和密码。"));
            return;
        }
        m_mode = Mode::Credentials;
        accept();
    });
    connect(qrButton, &QPushButton::clicked, this, [this]() {
        m_mode = Mode::QrCode;
        accept();
    });
    connect(cancelButton, &QPushButton::clicked, this, &LoginDialog::reject);
    layout->addWidget(buttons);
}
