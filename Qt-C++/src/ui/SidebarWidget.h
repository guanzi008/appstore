#pragma once

#include <QFrame>
#include <QJsonArray>
#include <QJsonObject>
#include <QLabel>
#include <QVBoxLayout>

class QPushButton;

class SidebarWidget final : public QFrame
{
    Q_OBJECT
    Q_DISABLE_COPY(SidebarWidget)

public:
    explicit SidebarWidget(QWidget *parent = nullptr);

    void setGroups(const QJsonArray &groups, const QString &currentKey);
    void setOnlineApps(const QJsonArray &apps, int total, bool loading = false, const QString &message = {});
    void setLoginState(bool loggedIn, const QString &accountLabel);
    void setTaskState(const QString &taskKey, const QString &state, const QString &detail = {});
    void clearCurrentSelection();

signals:
    void loginRequested();
    void logoutRequested();
    void refreshMyAppsRequested();
    void loadMoreMyAppsRequested();
    void groupSelected(const QString &groupKey);
    void onlineAppSelected(const QJsonObject &app);

private:
    void buildUi();
    void renderGroups();
    QLabel *taskLabelForKey(const QString &taskKey) const;
    static QString taskTitle(const QString &taskKey);
    static QString taskStateText(const QString &state);
    static QLabel *makeSidebarHint(const QString &text, QWidget *parent);
    static QLabel *makeSidebarSubsection(const QString &text, QWidget *parent);

    QJsonArray m_groups;
    QJsonArray m_onlineApps;
    QString m_currentKey;
    QString m_selectedOnlineAppId;
    QString m_onlineAppsMessage;
    int m_onlineAppTotal = 0;
    QVBoxLayout *m_groupLayout = nullptr;
    QLabel *m_userNameLabel = nullptr;
    QLabel *m_loginStateLabel = nullptr;
    QLabel *m_loginTaskLabel = nullptr;
    QLabel *m_parseTaskLabel = nullptr;
    QLabel *m_captureTaskLabel = nullptr;
    QLabel *m_submitTaskLabel = nullptr;
    QPushButton *m_loginButton = nullptr;
    bool m_loggedIn = false;
    bool m_onlineAppsLoading = false;
};
