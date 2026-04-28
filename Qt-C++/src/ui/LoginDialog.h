#pragma once

#include <QDialog>

class QLineEdit;

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

    [[nodiscard]] Mode mode() const;
    [[nodiscard]] QString username() const;
    [[nodiscard]] QString password() const;

private:
    void buildUi();

    Mode m_mode = Mode::Credentials;
    QLineEdit *m_usernameEdit = nullptr;
    QLineEdit *m_passwordEdit = nullptr;
};
