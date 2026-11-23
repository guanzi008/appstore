#pragma once

#include "BridgeClient.h"

#include <QDialog>

class QLabel;
class QPlainTextEdit;
class QPushButton;
class QTableWidget;

class UpdateHistoryDialog final : public QDialog
{
public:
    UpdateHistoryDialog(const QString &appId, const QString &appName, const QString &account, QWidget *parent);

private:
    void loadPage(int page);
    void loadSelectedNotes();
    void showPage(const QJsonObject &history);
    void showNotes(const QJsonObject &data);
    void updateControls();
    int pageCount() const;
    QString selectedDetailId() const;

    BridgeClient m_bridge;
    QString m_appId;
    QString m_account;
    int m_page = 0;
    int m_requestedPage = 1;
    int m_total = 0;
    QTableWidget *m_table = nullptr;
    QPlainTextEdit *m_notes = nullptr;
    QLabel *m_status = nullptr;
    QPushButton *m_previousButton = nullptr;
    QPushButton *m_nextButton = nullptr;
    QPushButton *m_retryButton = nullptr;
    QPushButton *m_notesButton = nullptr;
};
