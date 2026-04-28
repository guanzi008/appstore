#include "ui/SidebarWidget.h"

#include "core/AppJson.h"

#include <QHBoxLayout>
#include <QJsonObject>
#include <QJsonValue>
#include <QPushButton>
#include <QScrollArea>
#include <QStyle>
#include <QFontMetrics>
#include <QtGlobal>
#include <QWidget>

namespace {

QLabel *makeTaskLabel(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("TaskState"));
    label->setProperty("state", QStringLiteral("idle"));
    return label;
}

QString elidedSidebarText(const QWidget *widget, const QString &text, int width = 210)
{
    return widget->fontMetrics().elidedText(text.simplified(), Qt::ElideRight, width);
}

QString appRowText(const QWidget *widget, const QString &title, const QString &status)
{
    const QString statusText = status.trimmed();
    if (statusText.isEmpty()) {
        return elidedSidebarText(widget, QStringLiteral("📦 %1").arg(title));
    }
    const int statusWidth = widget->fontMetrics().horizontalAdvance(statusText) + 18;
    const int titleWidth = qMax(90, 188 - statusWidth);
    return QStringLiteral("📦 %1  %2").arg(
        widget->fontMetrics().elidedText(title.simplified(), Qt::ElideRight, titleWidth),
        statusText);
}

QString appIdentity(const QJsonObject &app)
{
    return app.value(QStringLiteral("app_id")).toString(app.value(QStringLiteral("id")).toString()).trimmed();
}

QString displayAppTitle(const QJsonObject &app)
{
    const QString appName = app.value(QStringLiteral("app_name")).toString().trimmed();
    const QString pkgName = app.value(QStringLiteral("pkg_name")).toString().trimmed();
    if (!appName.isEmpty()) {
        return appName;
    }
    if (!pkgName.isEmpty()) {
        return pkgName;
    }
    return QStringLiteral("未命名应用");
}

QString onlineAppSubtitle(const QJsonObject &app)
{
    const QString status = app.value(QStringLiteral("status_str")).toString().trimmed();
    if (!status.isEmpty()) {
        return status;
    }
    const QString code = app.value(QStringLiteral("status")).toString().trimmed();
    if (code == QStringLiteral("1")) {
        return QStringLiteral("草稿");
    }
    if (code == QStringLiteral("2")) {
        return QStringLiteral("审核中");
    }
    if (code == QStringLiteral("3")) {
        return QStringLiteral("已上架");
    }
    if (code == QStringLiteral("4")) {
        return QStringLiteral("已驳回");
    }
    if (code == QStringLiteral("5")) {
        return QStringLiteral("已下架");
    }
    if (code == QStringLiteral("101")) {
        return QStringLiteral("上架");
    }
    if (code == QStringLiteral("501")) {
        return QStringLiteral("推仓失败");
    }
    return QStringLiteral("状态未知");
}

} // namespace

SidebarWidget::SidebarWidget(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("Sidebar"));
    setFixedWidth(282);
    buildUi();
}

void SidebarWidget::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(22, 22, 22, 16);
    layout->setSpacing(10);

    auto *brandRow = new QHBoxLayout();
    brandRow->setSpacing(10);
    auto *brandIcon = new QLabel(QStringLiteral("UT"), this);
    brandIcon->setObjectName(QStringLiteral("BrandIcon"));
    brandIcon->setAlignment(Qt::AlignCenter);
    brandIcon->setFixedSize(46, 46);
    auto *brandTitle = new QLabel(QStringLiteral("UTPublisher"), this);
    brandTitle->setObjectName(QStringLiteral("BrandTitle"));
    brandRow->addWidget(brandIcon);
    brandRow->addWidget(brandTitle, 1);
    layout->addLayout(brandRow);

    auto *myAppsButton = new QPushButton(style()->standardIcon(QStyle::SP_DirOpenIcon), QStringLiteral("📁 我的应用"), this);
    myAppsButton->setObjectName(QStringLiteral("SidebarPrimary"));
    myAppsButton->setCursor(Qt::PointingHandCursor);
    connect(myAppsButton, &QPushButton::clicked, this, &SidebarWidget::refreshMyAppsRequested);
    layout->addWidget(myAppsButton);

    auto *appsScroll = new QScrollArea(this);
    appsScroll->setObjectName(QStringLiteral("FlatScroll"));
    appsScroll->setWidgetResizable(true);
    appsScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    appsScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    appsScroll->setMinimumHeight(220);
    auto *appsContainer = new QWidget(appsScroll);
    m_groupLayout = new QVBoxLayout(appsContainer);
    m_groupLayout->setContentsMargins(0, 0, 4, 0);
    m_groupLayout->setSpacing(5);
    m_groupLayout->setAlignment(Qt::AlignTop);
    appsScroll->setWidget(appsContainer);
    layout->addWidget(appsScroll, 1);

    auto *queueTitle = new QLabel(QStringLiteral("⌛ 任务队列"), this);
    queueTitle->setObjectName(QStringLiteral("SidebarSection"));
    layout->addWidget(queueTitle);

    auto *taskCard = new QFrame(this);
    taskCard->setObjectName(QStringLiteral("TaskCard"));
    auto *taskLayout = new QVBoxLayout(taskCard);
    taskLayout->setContentsMargins(12, 10, 12, 10);
    taskLayout->setSpacing(7);
    m_loginTaskLabel = makeTaskLabel(QStringLiteral("🔐 登录  ·  等待"), taskCard);
    m_parseTaskLabel = makeTaskLabel(QStringLiteral("📦 解析  ·  等待"), taskCard);
    m_captureTaskLabel = makeTaskLabel(QStringLiteral("🖼️ 截图  ·  等待"), taskCard);
    m_submitTaskLabel = makeTaskLabel(QStringLiteral("🚀 提交  ·  等待"), taskCard);
    taskLayout->addWidget(m_loginTaskLabel);
    taskLayout->addWidget(m_parseTaskLabel);
    taskLayout->addWidget(m_captureTaskLabel);
    taskLayout->addWidget(m_submitTaskLabel);
    layout->addWidget(taskCard);

    auto *settingsButton = new QPushButton(style()->standardIcon(QStyle::SP_FileDialogInfoView), QStringLiteral("ℹ️ 偏好设置"), this);
    settingsButton->setObjectName(QStringLiteral("SidebarPlain"));
    settingsButton->setCursor(Qt::PointingHandCursor);
    layout->addWidget(settingsButton);
    layout->addStretch(1);

    auto *userCard = new QFrame(this);
    userCard->setObjectName(QStringLiteral("UserCard"));
    auto *userLayout = new QHBoxLayout(userCard);
    userLayout->setContentsMargins(12, 10, 12, 10);
    userLayout->setSpacing(10);

    auto *avatar = new QLabel(QStringLiteral("U"), userCard);
    avatar->setObjectName(QStringLiteral("Avatar"));
    avatar->setAlignment(Qt::AlignCenter);
    avatar->setFixedSize(40, 40);
    userLayout->addWidget(avatar);

    auto *textLayout = new QVBoxLayout();
    textLayout->setContentsMargins(0, 0, 0, 0);
    textLayout->setSpacing(4);
    m_userNameLabel = new QLabel(QStringLiteral("未登录"), userCard);
    m_userNameLabel->setObjectName(QStringLiteral("UserName"));
    m_loginStateLabel = new QLabel(QStringLiteral("等待登录"), userCard);
    m_loginStateLabel->setObjectName(QStringLiteral("LoginState"));
    textLayout->addWidget(m_userNameLabel);
    textLayout->addWidget(m_loginStateLabel);
    userLayout->addLayout(textLayout, 1);

    m_loginButton = new QPushButton(QStringLiteral("登录"), userCard);
    m_loginButton->setObjectName(QStringLiteral("MiniButton"));
    m_loginButton->setCursor(Qt::PointingHandCursor);
    connect(m_loginButton, &QPushButton::clicked, this, [this]() {
        if (m_loggedIn) {
            emit logoutRequested();
            return;
        }
        emit loginRequested();
    });
    userLayout->addWidget(m_loginButton);
    layout->addWidget(userCard);

    renderGroups();
}

void SidebarWidget::setGroups(const QJsonArray &groups, const QString &currentKey)
{
    m_groups = groups;
    m_currentKey = currentKey;
    renderGroups();
}

void SidebarWidget::setOnlineApps(const QJsonArray &apps, int total, bool loading, const QString &message)
{
    m_onlineApps = apps;
    m_onlineAppTotal = total;
    m_onlineAppsLoading = loading;
    m_onlineAppsMessage = message;
    renderGroups();
}

void SidebarWidget::setLoginState(bool loggedIn, const QString &accountLabel)
{
    m_loggedIn = loggedIn;
    m_userNameLabel->setText(loggedIn ? accountLabel : QStringLiteral("未登录"));
    m_loginStateLabel->setText(loggedIn ? QStringLiteral("登录成功（Cookie 有效）") : QStringLiteral("输入账号密码或扫码登录"));
    m_loginButton->setText(loggedIn ? QStringLiteral("登出") : QStringLiteral("登录"));
    setTaskState(QStringLiteral("login"), loggedIn ? QStringLiteral("done") : QStringLiteral("idle"), loggedIn ? QStringLiteral("会话有效") : QStringLiteral("等待凭据"));
}

void SidebarWidget::setTaskState(const QString &taskKey, const QString &state, const QString &detail)
{
    QLabel *label = taskLabelForKey(taskKey);
    if (label == nullptr) {
        return;
    }

    const QString title = taskTitle(taskKey);
    const QString stateText = taskStateText(state);
    label->setText(detail.isEmpty()
                       ? QStringLiteral("%1  ·  %2").arg(title, stateText)
                       : QStringLiteral("%1  ·  %2  ·  %3").arg(title, stateText, detail));
    label->setProperty("state", state);
    label->style()->unpolish(label);
    label->style()->polish(label);
    label->update();
}

void SidebarWidget::renderGroups()
{
    while (QLayoutItem *item = m_groupLayout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->deleteLater();
        }
        delete item;
    }

    if (!m_groups.isEmpty()) {
        QJsonObject group;
        for (const QJsonValue &value : m_groups) {
            const QJsonObject candidate = value.toObject();
            if (candidate.value(QStringLiteral("key")).toString() == m_currentKey) {
                group = candidate;
                break;
            }
        }
        if (group.isEmpty()) {
            group = m_groups.first().toObject();
        }

        const QString key = group.value(QStringLiteral("key")).toString();
        const QString arches = AppJson::displayArches(group);
        const bool onlineOnly = group.value(QStringLiteral("online_only")).toBool(false);
        const QString status = group.value(QStringLiteral("status_str")).toString().trimmed();
        const QString subtitle = status.isEmpty() ? arches : status;
        if (!onlineOnly) {
            auto *button = new QPushButton(
                elidedSidebarText(this, QStringLiteral("📦 %1  %2").arg(AppJson::displayName(group), subtitle)),
                this);
            button->setProperty("class", QStringLiteral("AppRow"));
            button->setCheckable(true);
            button->setChecked(true);
            button->setCursor(Qt::PointingHandCursor);
            button->setMinimumHeight(36);
            connect(button, &QPushButton::clicked, this, [this, key]() {
                emit groupSelected(key);
            });
            m_groupLayout->addWidget(button);
        }
    }

    if (m_onlineApps.isEmpty()) {
        if (m_onlineAppsLoading) {
            m_groupLayout->addWidget(makeSidebarHint(QStringLiteral("正在加载我的应用..."), this));
        } else if (!m_onlineAppsMessage.isEmpty()) {
            m_groupLayout->addWidget(makeSidebarHint(m_onlineAppsMessage, this));
        } else if (!m_loggedIn) {
            m_groupLayout->addWidget(makeSidebarHint(QStringLiteral("登录后同步我的应用"), this));
        } else {
            m_groupLayout->addWidget(makeSidebarHint(QStringLiteral("暂无我的应用"), this));
        }
    } else {
        const int visibleCount = m_onlineApps.size();
        for (int index = 0; index < visibleCount; ++index) {
            const QJsonObject app = m_onlineApps.at(index).toObject();
            const QString appId = appIdentity(app);
            const QString pkgName = app.value(QStringLiteral("pkg_name")).toString().trimmed();
            const QString subtitle = onlineAppSubtitle(app);
            const QString title = displayAppTitle(app);
            auto *button = new QPushButton(appRowText(this, title, subtitle), this);
            button->setProperty("class", QStringLiteral("OnlineAppRow"));
            button->setCheckable(true);
            button->setChecked(!appId.isEmpty() && appId == m_selectedOnlineAppId);
            button->setCursor(Qt::PointingHandCursor);
            button->setMinimumHeight(30);
            button->setToolTip(QStringLiteral("%1\n%2\n状态：%3\napp_id: %4")
                                   .arg(title, pkgName, subtitle, app.value(QStringLiteral("app_id")).toString()));
            connect(button, &QPushButton::clicked, this, [this, app, appId]() {
                m_selectedOnlineAppId = appId;
                renderGroups();
                emit onlineAppSelected(app);
            });
            m_groupLayout->addWidget(button);
        }
        if (m_onlineAppsLoading) {
            m_groupLayout->addWidget(makeSidebarHint(QStringLiteral("正在加载更多..."), this));
        } else if (!m_onlineAppsMessage.isEmpty()) {
            m_groupLayout->addWidget(makeSidebarHint(m_onlineAppsMessage, this));
        } else if (m_onlineAppTotal > m_onlineApps.size()) {
            auto *moreButton = new QPushButton(QStringLiteral("加载更多  %1/%2").arg(m_onlineApps.size()).arg(m_onlineAppTotal), this);
            moreButton->setObjectName(QStringLiteral("SidebarPlain"));
            moreButton->setCursor(Qt::PointingHandCursor);
            connect(moreButton, &QPushButton::clicked, this, &SidebarWidget::loadMoreMyAppsRequested);
            m_groupLayout->addWidget(moreButton);
        }
    }
}

QLabel *SidebarWidget::taskLabelForKey(const QString &taskKey) const
{
    if (taskKey == QStringLiteral("login")) {
        return m_loginTaskLabel;
    }
    if (taskKey == QStringLiteral("parse")) {
        return m_parseTaskLabel;
    }
    if (taskKey == QStringLiteral("capture")) {
        return m_captureTaskLabel;
    }
    if (taskKey == QStringLiteral("submit")) {
        return m_submitTaskLabel;
    }
    return nullptr;
}

QString SidebarWidget::taskTitle(const QString &taskKey)
{
    if (taskKey == QStringLiteral("login")) {
        return QStringLiteral("🔐 登录");
    }
    if (taskKey == QStringLiteral("parse")) {
        return QStringLiteral("📦 解析");
    }
    if (taskKey == QStringLiteral("capture")) {
        return QStringLiteral("🖼️ 截图");
    }
    if (taskKey == QStringLiteral("submit")) {
        return QStringLiteral("🚀 提交");
    }
    return taskKey;
}

QString SidebarWidget::taskStateText(const QString &state)
{
    if (state == QStringLiteral("running")) {
        return QStringLiteral("进行中");
    }
    if (state == QStringLiteral("done")) {
        return QStringLiteral("完成");
    }
    if (state == QStringLiteral("failed")) {
        return QStringLiteral("失败");
    }
    return QStringLiteral("等待");
}

QLabel *SidebarWidget::makeSidebarHint(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("SidebarHint"));
    label->setWordWrap(true);
    return label;
}

QLabel *SidebarWidget::makeSidebarSubsection(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("SidebarSubsection"));
    return label;
}
