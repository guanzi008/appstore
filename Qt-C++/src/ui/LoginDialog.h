#pragma once

#include <QDialog>

class QLineEdit;
class QCheckBox;

class LoginDialog final : public QDialog
{
    Q_OBJECT
    Q_DISABLE_COPY(LoginDialog)

public:
    enum class Mode {
        Credentials,
        QrCode,
    };

    explicit LoginDialog(QWidget *parent = nullptr);

    void setRememberedCredentials(const QString &username, const QString &password, bool rememberCredentials);

    [[nodiscard]] Mode mode() const;
    [[nodiscard]] QString username() const;
    [[nodiscard]] QString password() const;
    [[nodiscard]] bool rememberCredentials() const;

private:
    void buildUi();

    Mode m_mode = Mode::Credentials;
    QLineEdit *m_usernameEdit = nullptr;
    QLineEdit *m_passwordEdit = nullptr;
    QCheckBox *m_rememberCredentialsCheck = nullptr;
};
