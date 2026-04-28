#include "MainWindow.h"

#include "core/AppJson.h"
#include "ui/AppTheme.h"
#include "ui/LoginDialog.h"
#include "ui/MetadataPanel.h"
#include "ui/QrLoginDialog.h"
#include "ui/ScreenshotPanel.h"
#include "ui/SidebarWidget.h"
#include "ui/TargetMatrixPanel.h"
#include "ui/WorkflowBar.h"

#include <DTitlebar>
#include <QApplication>
#include <QCheckBox>
#include <QClipboard>
#include <QDialog>
#include <QDir>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QHBoxLayout>
#include <QImage>
#include <QJsonArray>
#include <QLabel>
#include <QLinearGradient>
#include <QMessageBox>
#include <QMimeData>
#include <QPainter>
#include <QPainterPath>
#include <QPushButton>
#include <QStandardPaths>
#include <QStyle>
#include <QUrl>
#include <QVBoxLayout>

namespace {

QStringList packagePathsInDirectory(const QString &directoryPath)
{
    const QDir directory(directoryPath);
    const QFileInfoList entries = directory.entryInfoList(
        {QStringLiteral("*.deb"), QStringLiteral("*.uab"), QStringLiteral("*.layer")},
        QDir::Files,
        QDir::Name);

    QStringList paths;
    for (const QFileInfo &entry : entries) {
        paths.append(entry.absoluteFilePath());
    }
    return paths;
}

bool isPackageFile(const QString &path)
{
    const QString suffix = QFileInfo(path).suffix().toLower();
    return suffix == QStringLiteral("deb") || suffix == QStringLiteral("uab") || suffix == QStringLiteral("layer");
}

using StepState = WorkflowBar::StepState;

QString commandBusyText(const QString &command)
{
    if (command == QStringLiteral("analyze")) {
        return QStringLiteral("正在解析包与商店匹配信息...");
    }
    if (command == QStringLiteral("preprocess_assets")) {
        return QStringLiteral("正在预处理图标与截图...");
    }
    if (command == QStringLiteral("capture_screenshots")) {
        return QStringLiteral("正在执行自动截图流程...");
    }
    if (command == QStringLiteral("submit")) {
        return QStringLiteral("正在提交，请等待后端返回结果...");
    }
    if (command == QStringLiteral("login_wechat_qr")) {
        return QStringLiteral("正在打开扫码登录窗口...");
    }
    if (command == QStringLiteral("login_credentials")) {
        return QStringLiteral("正在使用账号密码登录...");
    }
    if (command == QStringLiteral("logout")) {
        return QStringLiteral("正在登出并清理本地会话...");
    }
    if (command == QStringLiteral("sync_store_data")) {
        return QStringLiteral("正在同步商店能力与分类...");
    }
    if (command == QStringLiteral("list_my_apps")) {
        return QStringLiteral("正在加载我的应用...");
    }
    if (command == QStringLiteral("fetch_match_defaults")) {
        return QStringLiteral("正在读取应用详情...");
    }
    return QStringLiteral("正在调用后端...");
}

QString commandLabelText(const QString &command)
{
    if (command == QStringLiteral("bootstrap")) {
        return QStringLiteral("初始化");
    }
    if (command == QStringLiteral("analyze")) {
        return QStringLiteral("包解析");
    }
    if (command == QStringLiteral("preprocess_assets")) {
        return QStringLiteral("素材预处理");
    }
    if (command == QStringLiteral("capture_screenshots")) {
        return QStringLiteral("自动截图");
    }
    if (command == QStringLiteral("submit")) {
        return QStringLiteral("提交");
    }
    if (command == QStringLiteral("login_wechat_qr")) {
        return QStringLiteral("扫码登录");
    }
    if (command == QStringLiteral("login_credentials")) {
        return QStringLiteral("账号登录");
    }
    if (command == QStringLiteral("logout")) {
        return QStringLiteral("登出");
    }
    if (command == QStringLiteral("sync_store_data")) {
        return QStringLiteral("同步商店数据");
    }
    if (command == QStringLiteral("list_my_apps")) {
        return QStringLiteral("我的应用");
    }
    if (command == QStringLiteral("fetch_match_defaults")) {
        return QStringLiteral("应用详情");
    }
    return command;
}

QString friendlyFailureMessage(const QString &command, const QString &message)
{
    const QString compactMessage = message.simplified();
    if (command == QStringLiteral("capture_screenshots")) {
        if (compactMessage.contains(QStringLiteral("timed out waiting for window"))) {
            return QStringLiteral("自动截图失败：应用已启动但未匹配到窗口。请先手动添加截图，或调整截图流程后重试。");
        }
        if (compactMessage.size() > 180) {
            return QStringLiteral("自动截图失败：%1...").arg(compactMessage.left(160));
        }
    }
    const QString unsupportedLinglongPrefix = QStringLiteral("unsupported linglong package format:");
    if (compactMessage.startsWith(unsupportedLinglongPrefix)) {
        const QString path = compactMessage.mid(unsupportedLinglongPrefix.size()).trimmed();
        const QString fileName = QFileInfo(path).fileName();
        return QStringLiteral("不支持的 linglong 包格式：%1。请使用 .deb 或可解析的 .uab/.layer 包。")
            .arg(fileName.isEmpty() ? path : fileName);
    }

    const QString missingLinglongPrefix = QStringLiteral("missing Linglong metadata in");
    if (compactMessage.startsWith(missingLinglongPrefix)) {
        const QString path = compactMessage.mid(missingLinglongPrefix.size()).trimmed();
        const QString fileName = QFileInfo(path).fileName();
        return QStringLiteral("未找到 linglong 元数据：%1。请确认包内包含 info.json 或 linglong.meta。")
            .arg(fileName.isEmpty() ? path : fileName);
    }

    const QString label = commandLabelText(command);
    return QStringLiteral("%1 失败：%2").arg(label, compactMessage);
}

QString sidebarFailureDetail(const QString &command, const QString &message)
{
    const QString compactMessage = message.simplified();
    if (compactMessage.startsWith(QStringLiteral("unsupported linglong package format:"))) {
        return QStringLiteral("格式不支持");
    }
    if (compactMessage.startsWith(QStringLiteral("missing Linglong metadata in"))) {
        return QStringLiteral("缺少元数据");
    }
    if (command == QStringLiteral("analyze")) {
        return QStringLiteral("解析失败");
    }
    if (command == QStringLiteral("preprocess_assets") || command == QStringLiteral("capture_screenshots")) {
        return QStringLiteral("制图失败");
    }
    if (command == QStringLiteral("submit")) {
        return QStringLiteral("提交失败");
    }
    return QStringLiteral("会话无效");
}

bool shouldShowBlockingFailureDialog(const QString &command)
{
    return command == QStringLiteral("submit")
        || command == QStringLiteral("login_wechat_qr")
        || command == QStringLiteral("login_credentials")
        || command == QStringLiteral("logout");
}

bool groupHasPackages(const QJsonObject &group)
{
    const QJsonArray packages = group.value(QStringLiteral("packages")).toArray();
    for (const QJsonValue &value : packages) {
        const QJsonObject package = value.toObject();
        const QString path = package.value(QStringLiteral("path")).toString().trimmed();
        if (!path.isEmpty() && !path.startsWith(QStringLiteral("online://")) && !package.value(QStringLiteral("online")).toBool(false)) {
            return true;
        }
    }
    return false;
}

QString selectedMatchId(const QJsonObject &group)
{
    QString appId = group.value(QStringLiteral("selected_match_app_id")).toString().trimmed();
    if (!appId.isEmpty()) {
        return appId;
    }
    const QJsonArray matches = group.value(QStringLiteral("existing_matches")).toArray();
    if (matches.size() == 1) {
        appId = matches.first().toObject().value(QStringLiteral("app_id")).toString().trimmed();
    }
    return appId;
}

bool groupTargetsExistingApp(const QJsonObject &group)
{
    return group.value(QStringLiteral("online_only")).toBool(false) || !selectedMatchId(group).isEmpty();
}

QJsonObject copyEditableOnlineFields(QJsonObject target, const QJsonObject &source)
{
    static const QStringList alwaysCopyKeys = {
        QStringLiteral("app_name_zh"),
        QStringLiteral("website"),
        QStringLiteral("short_desc_zh"),
        QStringLiteral("full_desc_zh"),
        QStringLiteral("note_zh"),
        QStringLiteral("app_name_en"),
        QStringLiteral("short_desc_en"),
        QStringLiteral("full_desc_en"),
        QStringLiteral("note_en"),
        QStringLiteral("manual_en_edited"),
        QStringLiteral("metadata_edited"),
        QStringLiteral("category_id"),
        QStringLiteral("region_codes"),
        QStringLiteral("replace_assets"),
        QStringLiteral("existing_matches"),
        QStringLiteral("selected_match_app_id"),
        QStringLiteral("submission_mode"),
        QStringLiteral("cpu_clip_options"),
        QStringLiteral("cpu_clip_codes"),
        QStringLiteral("motherboard_options"),
        QStringLiteral("motherboard_codes"),
    };
    for (const QString &key : alwaysCopyKeys) {
        if (source.contains(key)) {
            target.insert(key, source.value(key));
        }
    }

    if (!target.contains(QStringLiteral("icon_path")) || target.value(QStringLiteral("icon_path")).toString().trimmed().isEmpty()) {
        target.insert(QStringLiteral("icon_path"), source.value(QStringLiteral("icon_path")));
    }
    return target;
}

QJsonObject packageByPath(const QJsonObject &group, const QString &packagePath)
{
    const QJsonArray packages = group.value(QStringLiteral("packages")).toArray();
    for (const QJsonValue &value : packages) {
        const QJsonObject package = value.toObject();
        if (package.value(QStringLiteral("path")).toString() == packagePath) {
            return package;
        }
    }
    return packages.isEmpty() ? QJsonObject{} : packages.first().toObject();
}

QString firstPackagePath(const QJsonObject &group)
{
    return packageByPath(group, {}).value(QStringLiteral("path")).toString();
}

QString effectiveSelectedPackagePath(const QJsonObject &group, const QString &selectedPath)
{
    if (!selectedPath.trimmed().isEmpty()) {
        const QJsonObject package = packageByPath(group, selectedPath);
        if (package.value(QStringLiteral("path")).toString() == selectedPath) {
            return selectedPath;
        }
    }
    const QString fromGroup = group.value(QStringLiteral("selected_package_path")).toString().trimmed();
    if (!fromGroup.isEmpty() && packageByPath(group, fromGroup).value(QStringLiteral("path")).toString() == fromGroup) {
        return fromGroup;
    }
    return firstPackagePath(group);
}

QString packageLabel(const QJsonObject &package)
{
    const QString fileName = package.value(QStringLiteral("file_name")).toString().trimmed();
    const QString pkgName = package.value(QStringLiteral("pkg_name")).toString().trimmed();
    const QString version = package.value(QStringLiteral("version")).toString().trimmed();
    const QString arch = package.value(QStringLiteral("arch")).toString().trimmed();
    QString title = pkgName.isEmpty() ? fileName : pkgName;
    if (!version.isEmpty() && !title.contains(version)) {
        title = QStringLiteral("%1 %2").arg(title, version);
    }
    if (title.isEmpty()) {
        return QStringLiteral("未选择包");
    }
    return arch.isEmpty() ? title : QStringLiteral("%1  ·  %2").arg(title, arch);
}

} // namespace

MainWindow::MainWindow(QWidget *parent)
    : Dtk::Widget::DMainWindow(parent)
    , m_bridge(new BridgeClient(QStringLiteral(APPSTORE_REPO_ROOT), this))
{
    setWindowTitle(QStringLiteral("UTPublisher"));
    setAcceptDrops(true);
    resize(1480, 820);
    setMinimumSize(1180, 720);

    buildUi();
    AppTheme::apply(this);

    connect(m_bridge, &BridgeClient::commandStarted, this, &MainWindow::handleBridgeStarted);
    connect(m_bridge, &BridgeClient::commandFinished, this, &MainWindow::handleBridgeFinished);
    connect(m_bridge, &BridgeClient::commandFailed, this, &MainWindow::handleBridgeFailed);

    bootstrap();
}

void MainWindow::buildUi()
{
    titlebar()->setTitle(QStringLiteral("UTPublisher"));
    titlebar()->setMenuVisible(false);
    titlebar()->setSeparatorVisible(false);
    titlebar()->setBackgroundTransparent(true);
    titlebar()->hide();
    setTitlebarShadowEnabled(false);
    setWindowRadius(18);

    auto *root = new QWidget(this);
    root->setObjectName(QStringLiteral("AppRoot"));
    auto *rootLayout = new QHBoxLayout(root);
    rootLayout->setContentsMargins(0, 0, 0, 0);
    rootLayout->setSpacing(0);
    setCentralWidget(root);

    m_sidebar = new SidebarWidget(root);
    rootLayout->addWidget(m_sidebar);
    connect(m_sidebar, &SidebarWidget::loginRequested, this, &MainWindow::loginByWechat);
    connect(m_sidebar, &SidebarWidget::logoutRequested, this, &MainWindow::logout);
    connect(m_sidebar, &SidebarWidget::refreshMyAppsRequested, this, &MainWindow::listMyApps);
    connect(m_sidebar, &SidebarWidget::loadMoreMyAppsRequested, this, &MainWindow::loadMoreMyApps);
    connect(m_sidebar, &SidebarWidget::onlineAppSelected, this, &MainWindow::selectOnlineApp);
    connect(m_sidebar, &SidebarWidget::groupSelected, this, [this](const QString &key) {
        persistCurrentGroupFromUi();
        const QJsonObject group = groupByKey(key);
        if (!group.isEmpty()) {
            setCurrentGroup(group);
        }
    });

    auto *workspace = new QFrame(root);
    workspace->setObjectName(QStringLiteral("Workspace"));
    auto *workspaceLayout = new QVBoxLayout(workspace);
    workspaceLayout->setContentsMargins(16, 10, 16, 12);
    workspaceLayout->setSpacing(8);
    rootLayout->addWidget(workspace, 1);

    auto *header = new QHBoxLayout();
    header->setSpacing(12);
    m_titleLabel = new QLabel(QStringLiteral("📦 应用管理 - 新建版本发布"), workspace);
    m_titleLabel->setObjectName(QStringLiteral("PageTitle"));
    header->addWidget(m_titleLabel, 1);

    m_capabilityLabel = new QLabel(QStringLiteral("能力缓存：校验中"), workspace);
    m_capabilityLabel->setObjectName(QStringLiteral("MutedText"));
    m_capabilityLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    header->addWidget(m_capabilityLabel);

    m_autoPilotCheck = new QCheckBox(QStringLiteral("🚀 Auto-Pilot（全自动提交）"), workspace);
    m_autoPilotCheck->setCursor(Qt::PointingHandCursor);
    header->addWidget(m_autoPilotCheck);

    auto *syncButton = new QPushButton(style()->standardIcon(QStyle::SP_BrowserReload), QStringLiteral("同步"), workspace);
    syncButton->setCursor(Qt::PointingHandCursor);
    connect(syncButton, &QPushButton::clicked, this, &MainWindow::syncStoreData);
    header->addWidget(syncButton);
    workspaceLayout->addLayout(header);

    auto *content = new QHBoxLayout();
    content->setSpacing(10);
    workspaceLayout->addLayout(content, 1);

    auto *leftColumn = new QVBoxLayout();
    leftColumn->setSpacing(8);
    content->addLayout(leftColumn, 1);

    auto *dropZone = new QFrame(workspace);
    dropZone->setObjectName(QStringLiteral("DropZone"));
    dropZone->setFixedHeight(62);
    auto *dropLayout = new QVBoxLayout(dropZone);
    dropLayout->setContentsMargins(12, 6, 12, 6);
    dropLayout->setSpacing(2);
    m_dropHintLabel = new QLabel(QStringLiteral("📥 拖拽 .deb / linglong 文件到这里进行智能解析"), dropZone);
    m_dropHintLabel->setObjectName(QStringLiteral("DropHint"));
    m_dropHintLabel->setAlignment(Qt::AlignCenter);
    auto *chooseButton = new QPushButton(style()->standardIcon(QStyle::SP_FileDialogNewFolder), QStringLiteral("选择包文件"), dropZone);
    chooseButton->setCursor(Qt::PointingHandCursor);
    chooseButton->setMaximumWidth(150);
    connect(chooseButton, &QPushButton::clicked, this, &MainWindow::choosePackages);
    dropLayout->addStretch(1);
    dropLayout->addWidget(m_dropHintLabel);
    dropLayout->addWidget(chooseButton, 0, Qt::AlignHCenter);
    dropLayout->addStretch(1);
    leftColumn->addWidget(dropZone);

    m_metadataPanel = new MetadataPanel(workspace);
    connect(m_metadataPanel, &MetadataPanel::packageSelected, this, &MainWindow::selectPackage);
    leftColumn->addWidget(m_metadataPanel, 1);

    auto *rightPane = new QFrame(workspace);
    rightPane->setObjectName(QStringLiteral("RightPane"));
    rightPane->setMinimumWidth(430);
    rightPane->setMaximumWidth(500);
    auto *rightColumn = new QVBoxLayout(rightPane);
    rightColumn->setContentsMargins(0, 0, 0, 0);
    rightColumn->setSpacing(8);
    content->addWidget(rightPane);

    m_screenshotPanel = new ScreenshotPanel(rightPane);
    connect(m_screenshotPanel, &ScreenshotPanel::addFilesRequested, this, &MainWindow::chooseScreenshotFiles);
    connect(m_screenshotPanel, &ScreenshotPanel::pasteRequested, this, &MainWindow::pasteScreenshotFromClipboard);
    connect(m_screenshotPanel, &ScreenshotPanel::placeholderRequested, this, &MainWindow::generatePlaceholderScreenshots);
    connect(m_screenshotPanel, &ScreenshotPanel::captureRequested, this, &MainWindow::captureScreenshots);
    connect(m_screenshotPanel, &ScreenshotPanel::preprocessRequested, this, &MainWindow::preprocessAssets);
    connect(m_screenshotPanel, &ScreenshotPanel::removeScreenshotRequested, this, &MainWindow::removeScreenshotAt);
    rightColumn->addWidget(m_screenshotPanel);

    m_targetPanel = new TargetMatrixPanel(rightPane);
    rightColumn->addWidget(m_targetPanel, 1);

    m_workflowBar = new WorkflowBar(workspace);
    connect(m_workflowBar, &WorkflowBar::submitRequested, this, &MainWindow::submitCurrentGroup);
    workspaceLayout->addWidget(m_workflowBar);
}

void MainWindow::bootstrap()
{
    runBridgeCommand(QStringLiteral("bootstrap"), {}, 120000);
}

void MainWindow::runBridgeCommand(const QString &command, const QJsonObject &payload, int timeoutMs)
{
    if (m_bridge->isBusy()) {
        QMessageBox::information(this, QStringLiteral("后端忙碌"), QStringLiteral("当前后端任务尚未结束。"));
        return;
    }
    m_bridge->callAsync(command, payload, timeoutMs);
}

void MainWindow::handleBridgeStarted(const QString &command)
{
    QApplication::setOverrideCursor(Qt::BusyCursor);
    m_workflowBar->setBusy(true);
    m_workflowBar->setStatusText(commandBusyText(command));

    if (command == QStringLiteral("bootstrap") || command == QStringLiteral("login_wechat_qr") || command == QStringLiteral("login_credentials")
        || command == QStringLiteral("logout")) {
        QString detail = QStringLiteral("验证凭据");
        if (command == QStringLiteral("bootstrap")) {
            detail = QStringLiteral("校验会话");
        } else if (command == QStringLiteral("logout")) {
            detail = QStringLiteral("清理会话");
        }
        m_sidebar->setTaskState(QStringLiteral("login"), QStringLiteral("running"), detail);
    } else if (command == QStringLiteral("analyze")) {
        m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("running"), QStringLiteral("解析包信息"));
        m_workflowBar->setStepStates(StepState::Running, StepState::Idle, StepState::Idle, StepState::Idle);
    } else if (command == QStringLiteral("preprocess_assets") || command == QStringLiteral("capture_screenshots")) {
        m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("running"), command == QStringLiteral("capture_screenshots") ? QStringLiteral("执行自动截图") : QStringLiteral("预处理素材"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Running, StepState::Idle, StepState::Idle);
    } else if (command == QStringLiteral("submit")) {
        m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("running"), QStringLiteral("提交上传任务"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Done, StepState::Running);
    } else if (command == QStringLiteral("list_my_apps")) {
        m_sidebar->setOnlineApps(m_myAppsLoadingMore ? m_myAppsRows : QJsonArray{}, m_myAppsTotal, true);
    } else if (command == QStringLiteral("fetch_match_defaults")) {
        m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("running"), QStringLiteral("读取应用详情"));
    }
}

void MainWindow::handleBridgeFinished(const QString &command, const QJsonObject &data)
{
    QApplication::restoreOverrideCursor();
    m_workflowBar->setBusy(false);

    if (command == QStringLiteral("bootstrap") || command == QStringLiteral("login_wechat_qr")
        || command == QStringLiteral("login_credentials") || command == QStringLiteral("logout")
        || command == QStringLiteral("sync_store_data")) {
        if (command == QStringLiteral("logout")) {
            m_preferredAccount.clear();
        }
        applySessionData(data);
        appendLogs(data.value(QStringLiteral("logs")).toArray());
        if (command == QStringLiteral("logout")) {
            m_workflowBar->setStatusText(QStringLiteral("已登出，本地登录缓存已清理。"));
            m_myAppsRows = {};
            m_myAppsPage = 0;
            m_myAppsTotal = 0;
            m_sidebar->setOnlineApps({}, 0, false);
        } else if (command == QStringLiteral("bootstrap")) {
            m_workflowBar->setStatusText(QStringLiteral("登录缓存校验完成。"));
        } else if (command == QStringLiteral("sync_store_data")) {
            m_workflowBar->setStatusText(QStringLiteral("商店数据已更新。"));
        } else {
            m_workflowBar->setStatusText(QStringLiteral("登录成功，会话已保存。"));
        }
        const bool loggedIn = data.value(QStringLiteral("login")).toObject().value(QStringLiteral("logged_in")).toBool(false);
        if (loggedIn && command != QStringLiteral("logout")) {
            listMyApps();
        }
        return;
    }

    if (command == QStringLiteral("list_my_apps")) {
        const QJsonObject login = data.value(QStringLiteral("login")).toObject();
        const QString sessionAccount = login.value(QStringLiteral("session_account")).toString();
        const QString accountLabel = login.value(QStringLiteral("account_label")).toString();
        if (!sessionAccount.isEmpty()) {
            m_preferredAccount = sessionAccount;
        } else if (!accountLabel.isEmpty()) {
            m_preferredAccount = accountLabel;
        }
        m_sidebar->setLoginState(login.value(QStringLiteral("logged_in")).toBool(false), accountLabel);
        const QJsonArray rows = data.value(QStringLiteral("rows")).toArray();
        m_myAppsTotal = data.value(QStringLiteral("total")).toInt(rows.size());
        if (m_myAppsLoadingMore) {
            for (const QJsonValue &row : rows) {
                m_myAppsRows.append(row);
            }
            ++m_myAppsPage;
        } else {
            m_myAppsRows = rows;
        }
        m_myAppsLoadingMore = false;
        m_sidebar->setOnlineApps(m_myAppsRows, m_myAppsTotal, false);
        m_workflowBar->setStatusText(QStringLiteral("已加载 %1/%2 个我的应用。").arg(m_myAppsRows.size()).arg(m_myAppsTotal));
        return;
    }

    if (command == QStringLiteral("fetch_match_defaults")) {
        applySessionData(data);
        QJsonObject onlineGroup = data.value(QStringLiteral("group")).toObject();
        if (onlineGroup.isEmpty()) {
            m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("failed"), QStringLiteral("无应用详情"));
            m_workflowBar->setStatusText(QStringLiteral("未能读取应用详情。"));
            return;
        }

        const QString appName = AppJson::displayName(onlineGroup);
        if (!m_currentGroup.isEmpty() && groupHasPackages(m_currentGroup)) {
            QJsonObject packageGroup = currentGroupFromUi();
            packageGroup = copyEditableOnlineFields(packageGroup, onlineGroup);
            setCurrentGroup(packageGroup);
        } else {
            setCurrentGroup(onlineGroup);
        }
        m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("done"), QStringLiteral("已选择应用"));
        m_workflowBar->setStepStates(StepState::Idle, StepState::Idle, StepState::Idle, StepState::Idle);
        m_workflowBar->setStatusText(QStringLiteral("已选择我的应用：%1。可直接提交文案、截图或适配项更新；拖入新包则更新安装包。").arg(appName));
        return;
    }

    if (command == QStringLiteral("analyze")) {
        applySessionData(data);
        m_groups = data.value(QStringLiteral("groups")).toArray();
        if (!m_groups.isEmpty()) {
            setCurrentGroup(m_groups.first().toObject());
            m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("done"), QStringLiteral("当前应用"));
            m_workflowBar->setStepStates(StepState::Done, StepState::Idle, StepState::Idle, StepState::Idle);
            m_workflowBar->setStatusText(QStringLiteral("解析完成，已载入当前应用。"));
            if (m_autoPilotCheck->isChecked()) {
                preprocessAssets();
            }
        } else {
            m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("failed"), QStringLiteral("无应用"));
            m_workflowBar->setStatusText(QStringLiteral("解析完成，但未得到可发布应用。"));
            renderSidebar();
        }
        return;
    }

    if (command == QStringLiteral("preprocess_assets") || command == QStringLiteral("capture_screenshots")) {
        const QJsonObject group = data.value(QStringLiteral("group")).toObject();
        if (!group.isEmpty()) {
            setCurrentGroup(group);
        }
        appendLogs(data.value(QStringLiteral("logs")).toArray());
        m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), command == QStringLiteral("capture_screenshots") ? QStringLiteral("截图已返回") : QStringLiteral("素材已准备"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
        m_workflowBar->setStatusText(command == QStringLiteral("capture_screenshots") ? QStringLiteral("截图流程完成。") : QStringLiteral("素材预处理完成。"));
        return;
    }

    if (command == QStringLiteral("submit")) {
        applySessionData(data);
        appendLogs(data.value(QStringLiteral("logs")).toArray());
        const QJsonObject report = data.value(QStringLiteral("report")).toObject();
        const QString reportPath = report.value(QStringLiteral("report_path")).toString();
        m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("done"), QStringLiteral("当前应用"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Done, StepState::Done);
        m_workflowBar->setStatusText(QStringLiteral("提交完成，报告：%1").arg(reportPath));
        QMessageBox::information(this, QStringLiteral("提交完成"), QStringLiteral("已提交当前应用。\n报告：%1").arg(reportPath));
        return;
    }
}

void MainWindow::handleBridgeFailed(const QString &command, const QString &message, const QString &traceback)
{
    QApplication::restoreOverrideCursor();
    m_workflowBar->setBusy(false);
    const QString friendlyMessage = friendlyFailureMessage(command, message);
    m_workflowBar->setStatusText(friendlyMessage);

    if (command == QStringLiteral("bootstrap") || command == QStringLiteral("login_wechat_qr") || command == QStringLiteral("login_credentials")
        || command == QStringLiteral("logout")) {
        m_sidebar->setTaskState(QStringLiteral("login"), QStringLiteral("failed"), sidebarFailureDetail(command, message));
    } else if (command == QStringLiteral("analyze")) {
        m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("failed"), sidebarFailureDetail(command, message));
        m_workflowBar->setStepStates(StepState::Failed, StepState::Idle, StepState::Idle, StepState::Idle);
    } else if (command == QStringLiteral("preprocess_assets") || command == QStringLiteral("capture_screenshots")) {
        m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("failed"), sidebarFailureDetail(command, message));
        m_workflowBar->setStepStates(StepState::Done, StepState::Failed, StepState::Idle, StepState::Idle);
    } else if (command == QStringLiteral("submit")) {
        m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("failed"), sidebarFailureDetail(command, message));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Done, StepState::Failed);
    } else if (command == QStringLiteral("list_my_apps")) {
        m_myAppsLoadingMore = false;
        m_sidebar->setOnlineApps(m_myAppsRows, m_myAppsTotal, false, QStringLiteral("我的应用加载失败"));
    } else if (command == QStringLiteral("fetch_match_defaults")) {
        m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("failed"), QStringLiteral("详情失败"));
    }

    m_recentLogs.append(QStringLiteral("%1: %2").arg(commandLabel(command), message));
    if (!traceback.isEmpty()) {
        m_recentLogs.append(traceback);
    }

    if (shouldShowBlockingFailureDialog(command)) {
        QMessageBox::warning(this, QStringLiteral("后端任务失败"), friendlyMessage);
    } else {
        sendMessage(style()->standardIcon(QStyle::SP_MessageBoxWarning), friendlyMessage);
    }
}

void MainWindow::applySessionData(const QJsonObject &data)
{
    const QJsonObject login = data.value(QStringLiteral("login")).toObject();
    const bool loggedIn = login.value(QStringLiteral("logged_in")).toBool(false);
    const QString accountLabel = login.value(QStringLiteral("account_label")).toString();
    const QString sessionAccount = login.value(QStringLiteral("session_account")).toString();
    if (!sessionAccount.isEmpty()) {
        m_preferredAccount = sessionAccount;
    } else if (!accountLabel.isEmpty()) {
        m_preferredAccount = accountLabel;
    }
    m_sidebar->setLoginState(loggedIn, accountLabel);

    const QJsonObject capabilities = data.value(QStringLiteral("capabilities")).toObject();
    if (capabilities.value(QStringLiteral("loaded")).toBool(false)) {
        m_capabilityLabel->setText(QStringLiteral("能力缓存： deb %1 / linglong %2 / baseline %3 / 适配 %4")
                                       .arg(capabilities.value(QStringLiteral("deb_system_line_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("linglong_system_line_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("baseline_group_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("cpu_clip_option_count")).toInt()
                                            + capabilities.value(QStringLiteral("motherboard_option_count")).toInt()));
    } else {
        m_capabilityLabel->setText(QStringLiteral("能力缓存：未加载"));
    }

    const QJsonArray categories = data.value(QStringLiteral("categories")).toArray();
    if (!categories.isEmpty()) {
        m_categories = categories;
    }
    m_metadataPanel->setCategories(m_categories);
}

void MainWindow::setCurrentGroup(const QJsonObject &group)
{
    m_currentGroup = normalizedGroup(groupWithSelectedOnlineApp(group));
    m_selectedPackagePath = effectiveSelectedPackagePath(m_currentGroup, m_selectedPackagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        m_currentGroup.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    const QString currentKey = m_currentGroup.value(QStringLiteral("key")).toString();
    if (!currentKey.isEmpty()) {
        bool replaced = false;
        for (int index = 0; index < m_groups.size(); ++index) {
            if (m_groups.at(index).toObject().value(QStringLiteral("key")).toString() == currentKey) {
                m_groups[index] = m_currentGroup;
                replaced = true;
                break;
            }
        }
        if (!replaced) {
            m_groups.append(m_currentGroup);
        }
    }
    renderCurrentGroup();
    renderSidebar();
}

void MainWindow::persistCurrentGroupFromUi()
{
    if (m_currentGroup.isEmpty()) {
        return;
    }
    const QJsonObject updated = currentGroupFromUi();
    m_selectedPackagePath = effectiveSelectedPackagePath(updated, m_selectedPackagePath);
    const QString key = updated.value(QStringLiteral("key")).toString();
    for (int index = 0; index < m_groups.size(); ++index) {
        if (m_groups.at(index).toObject().value(QStringLiteral("key")).toString() == key) {
            m_groups[index] = updated;
            break;
        }
    }
    m_currentGroup = updated;
}

void MainWindow::renderCurrentGroup()
{
    const bool hasGroup = !m_currentGroup.isEmpty();
    const bool onlineOnly = m_currentGroup.value(QStringLiteral("online_only")).toBool(false);
    const QString displayName = AppJson::displayName(m_currentGroup);
    m_titleLabel->setText(hasGroup
                              ? QStringLiteral("📦 应用管理 - %1").arg(displayName)
                              : QStringLiteral("📦 应用管理 - 新建版本发布"));
    if (onlineOnly) {
        m_dropHintLabel->setText(QStringLiteral("已选择我的应用：%1。可提交资料/截图/适配项更新，拖入新包则更新安装包").arg(displayName));
    } else {
        m_dropHintLabel->setText(hasGroup
                                     ? QStringLiteral("已载入 %1 个包：%2").arg(m_currentGroup.value(QStringLiteral("packages")).toArray().size()).arg(AppJson::stringValue(m_currentGroup, QStringLiteral("pkg_name")))
                                     : QStringLiteral("📥 拖拽 .deb / linglong 文件到这里进行智能解析"));
    }
    m_selectedPackagePath = effectiveSelectedPackagePath(m_currentGroup, m_selectedPackagePath);
    m_metadataPanel->setGroup(m_currentGroup);
    m_metadataPanel->setSelectedPackagePath(m_selectedPackagePath);
    const QJsonObject packageView = groupForSelectedPackageView(m_currentGroup);
    m_screenshotPanel->setGroup(packageView);
    m_targetPanel->setGroup(m_currentGroup);
    m_targetPanel->setSelectedPackagePath(m_selectedPackagePath);
}

void MainWindow::renderSidebar()
{
    m_sidebar->setGroups(m_groups, m_currentGroup.value(QStringLiteral("key")).toString());
}

void MainWindow::appendLogs(const QJsonArray &logs)
{
    for (const QJsonValue &value : logs) {
        const QString line = value.toString().trimmed();
        if (!line.isEmpty()) {
            m_recentLogs.append(line);
        }
    }
    while (m_recentLogs.size() > 200) {
        m_recentLogs.removeFirst();
    }
}

void MainWindow::selectPackage(const QString &packagePath)
{
    if (m_currentGroup.isEmpty()) {
        return;
    }
    QJsonObject group = currentGroupFromUi();
    m_selectedPackagePath = effectiveSelectedPackagePath(group, packagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    m_currentGroup = group;
    const QString label = packageLabel(packageByPath(group, m_selectedPackagePath));
    renderCurrentGroup();
    renderSidebar();
    m_workflowBar->setStatusText(QStringLiteral("已选择包：%1。右侧显示该包的截图与适配范围。").arg(label));
}

QJsonObject MainWindow::normalizedGroup(QJsonObject group) const
{
    if (group.value(QStringLiteral("submission_mode")).toString().isEmpty()) {
        group.insert(QStringLiteral("submission_mode"), QStringLiteral("auto"));
    }
    if (!group.contains(QStringLiteral("category_id"))) {
        group.insert(QStringLiteral("category_id"), QStringLiteral("1"));
    }
    if (!group.contains(QStringLiteral("app_name_zh"))) {
        group.insert(QStringLiteral("app_name_zh"), AppJson::displayName(group));
    }
    if (!group.contains(QStringLiteral("short_desc_zh"))) {
        group.insert(QStringLiteral("short_desc_zh"), group.value(QStringLiteral("short_description")).toString());
    }
    if (!group.contains(QStringLiteral("full_desc_zh"))) {
        group.insert(QStringLiteral("full_desc_zh"), group.value(QStringLiteral("full_description")).toString());
    }
    if (!group.contains(QStringLiteral("website"))) {
        group.insert(QStringLiteral("website"), group.value(QStringLiteral("homepage")).toString());
    }
    if (!group.contains(QStringLiteral("region_codes"))) {
        group.insert(QStringLiteral("region_codes"), QJsonArray{QStringLiteral("1")});
    }
    if (!group.contains(QStringLiteral("replace_assets"))) {
        const bool hasExistingMatches = !group.value(QStringLiteral("existing_matches")).toArray().isEmpty();
        group.insert(QStringLiteral("replace_assets"), !hasExistingMatches);
    }
    return group;
}

QJsonObject MainWindow::groupWithSelectedOnlineApp(QJsonObject group) const
{
    if (group.isEmpty() || m_selectedOnlineApp.isEmpty()) {
        return group;
    }

    const QString appId = AppJson::stringValue(m_selectedOnlineApp, QStringLiteral("app_id"), m_selectedOnlineApp.value(QStringLiteral("id")).toString());
    if (appId.isEmpty()) {
        return group;
    }
    if (m_currentGroup.value(QStringLiteral("online_only")).toBool(false) && selectedMatchId(m_currentGroup) == appId) {
        group = copyEditableOnlineFields(group, m_currentGroup);
    }
    const QString detailId = AppJson::stringValue(m_selectedOnlineApp, QStringLiteral("detail_id"), m_selectedOnlineApp.value(QStringLiteral("id")).toString());
    const QString pkgName = m_selectedOnlineApp.value(QStringLiteral("pkg_name")).toString().trimmed();
    const QString appName = m_selectedOnlineApp.value(QStringLiteral("app_name")).toString().trimmed();
    const QString status = m_selectedOnlineApp.value(QStringLiteral("status")).toString().trimmed();
    const QString statusText = m_selectedOnlineApp.value(QStringLiteral("status_str")).toString().trimmed();

    QJsonObject match;
    match.insert(QStringLiteral("app_id"), appId);
    match.insert(QStringLiteral("detail_id"), detailId);
    match.insert(QStringLiteral("pkg_name"), pkgName);
    match.insert(QStringLiteral("app_name"), appName);
    match.insert(QStringLiteral("status"), status);
    match.insert(QStringLiteral("status_str"), statusText);

    QJsonArray matches = group.value(QStringLiteral("existing_matches")).toArray();
    bool replaced = false;
    for (int index = 0; index < matches.size(); ++index) {
        if (matches.at(index).toObject().value(QStringLiteral("app_id")).toString() == appId) {
            matches[index] = match;
            replaced = true;
            break;
        }
    }
    if (!replaced) {
        matches.prepend(match);
    }
    group.insert(QStringLiteral("existing_matches"), matches);
    group.insert(QStringLiteral("selected_match_app_id"), appId);
    group.insert(QStringLiteral("status"), status);
    group.insert(QStringLiteral("status_str"), statusText);
    group.insert(QStringLiteral("submission_mode"), QStringLiteral("update"));
    return group;
}

QJsonObject MainWindow::currentGroupFromUi() const
{
    if (m_currentGroup.isEmpty()) {
        return {};
    }
    QJsonObject group = m_metadataPanel->groupFromUi(m_currentGroup);
    group = m_targetPanel->groupWithTargets(group);
    if (!m_selectedPackagePath.isEmpty()) {
        group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    return group;
}

QJsonObject MainWindow::groupForSelectedPackageView(const QJsonObject &group) const
{
    QJsonObject view = group;
    const QString selectedPath = effectiveSelectedPackagePath(group, m_selectedPackagePath);
    const QJsonObject package = packageByPath(group, selectedPath);
    if (!package.isEmpty()) {
        view.insert(QStringLiteral("selected_package_path"), selectedPath);
        view.insert(QStringLiteral("selected_package_label"), packageLabel(package));
        const QString packageIconPath = package.value(QStringLiteral("icon_path")).toString().trimmed();
        if (!packageIconPath.isEmpty()) {
            view.insert(QStringLiteral("selected_package_icon_path"), packageIconPath);
        }
    }
    return view;
}

QJsonObject MainWindow::groupByKey(const QString &key) const
{
    for (const QJsonValue &value : m_groups) {
        const QJsonObject group = value.toObject();
        if (group.value(QStringLiteral("key")).toString() == key) {
            return group;
        }
    }
    return {};
}

void MainWindow::choosePackages()
{
    const QStringList paths = QFileDialog::getOpenFileNames(
        this,
        QStringLiteral("选择待发布包"),
        QDir::homePath(),
        QStringLiteral("Packages (*.deb *.uab *.layer)"));
    analyzePackagePaths(paths);
}

void MainWindow::listMyApps()
{
    if (m_preferredAccount.isEmpty()) {
        m_myAppsRows = {};
        m_myAppsPage = 0;
        m_myAppsTotal = 0;
        m_sidebar->setOnlineApps({}, 0, false, QStringLiteral("登录后同步我的应用"));
        return;
    }
    m_myAppsLoadingMore = false;
    m_myAppsPage = 1;
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("page_num"), m_myAppsPage);
    payload.insert(QStringLiteral("page_size"), m_myAppsPageSize);
    runBridgeCommand(QStringLiteral("list_my_apps"), payload, 300000);
}

void MainWindow::loadMoreMyApps()
{
    if (m_preferredAccount.isEmpty()) {
        m_sidebar->setOnlineApps(m_myAppsRows, m_myAppsTotal, false, QStringLiteral("登录后同步我的应用"));
        return;
    }
    if (m_bridge->isBusy()) {
        QMessageBox::information(this, QStringLiteral("后端忙碌"), QStringLiteral("当前后端任务尚未结束。"));
        return;
    }
    if (m_myAppsTotal > 0 && m_myAppsRows.size() >= m_myAppsTotal) {
        return;
    }

    m_myAppsLoadingMore = true;
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("page_num"), m_myAppsPage + 1);
    payload.insert(QStringLiteral("page_size"), m_myAppsPageSize);
    runBridgeCommand(QStringLiteral("list_my_apps"), payload, 300000);
}

void MainWindow::loginByWechat()
{
    LoginDialog dialog(this);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    QJsonObject payload;
    if (dialog.mode() == LoginDialog::Mode::Credentials) {
        payload.insert(QStringLiteral("username"), dialog.username());
        payload.insert(QStringLiteral("password"), dialog.password());
        runBridgeCommand(QStringLiteral("login_credentials"), payload, 600000);
        return;
    }

    if (m_bridge->isBusy()) {
        QMessageBox::information(this, QStringLiteral("后端忙碌"), QStringLiteral("当前后端任务尚未结束。"));
        return;
    }

    m_sidebar->setTaskState(QStringLiteral("login"), QStringLiteral("running"), QStringLiteral("等待扫码"));
    m_workflowBar->setBusy(true);
    m_workflowBar->setStatusText(QStringLiteral("请在弹窗中使用微信扫码登录。"));
    QrLoginDialog qrDialog(QStringLiteral(APPSTORE_REPO_ROOT), m_preferredAccount, this);
    if (qrDialog.exec() != QDialog::Accepted) {
        m_workflowBar->setBusy(false);
        m_sidebar->setTaskState(QStringLiteral("login"), QStringLiteral("failed"), QStringLiteral("已取消"));
        m_workflowBar->setStatusText(QStringLiteral("扫码登录已取消。"));
        return;
    }

    const QJsonObject data = qrDialog.resultData();
    m_workflowBar->setBusy(false);
    applySessionData(data);
    appendLogs(data.value(QStringLiteral("logs")).toArray());
    m_sidebar->setTaskState(QStringLiteral("login"), QStringLiteral("done"), QStringLiteral("会话有效"));
    m_workflowBar->setStatusText(QStringLiteral("登录成功，会话已保存。"));
    if (data.value(QStringLiteral("login")).toObject().value(QStringLiteral("logged_in")).toBool(false)) {
        listMyApps();
    }
}

void MainWindow::logout()
{
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    runBridgeCommand(QStringLiteral("logout"), payload, 120000);
}

void MainWindow::syncStoreData()
{
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    runBridgeCommand(QStringLiteral("sync_store_data"), payload, 300000);
}

void MainWindow::preprocessAssets()
{
    const QJsonObject group = currentGroupFromUi();
    if (group.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
        return;
    }
    if (!groupHasPackages(group)) {
        QMessageBox::information(
            this,
            QStringLiteral("未选择本地包"),
            QStringLiteral("线上包已用于查看与复用素材；需要重新提取图标或自动制图时，请先拖入本地 .deb / linglong 包。"));
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("group"), group);
    runBridgeCommand(QStringLiteral("preprocess_assets"), payload, 300000);
}

void MainWindow::captureScreenshots()
{
    const QJsonObject group = currentGroupFromUi();
    if (group.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
        return;
    }
    if (!groupHasPackages(group)) {
        QMessageBox::information(
            this,
            QStringLiteral("未选择本地包"),
            QStringLiteral("自动截图需要可安装的本地包。线上已有截图会同步到右侧，可直接删除或添加替换素材。"));
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("group"), group);
    runBridgeCommand(QStringLiteral("capture_screenshots"), payload, 900000);
}

void MainWindow::chooseScreenshotFiles()
{
    if (m_currentGroup.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
        return;
    }
    const QStringList paths = QFileDialog::getOpenFileNames(
        this,
        QStringLiteral("选择应用截图"),
        QDir::homePath(),
        QStringLiteral("Images (*.png *.jpg *.jpeg *.webp *.bmp)"));
    if (paths.isEmpty()) {
        return;
    }

    QJsonObject group = currentGroupFromUi();
    QJsonArray screenshots = group.value(QStringLiteral("screenshot_paths")).toArray();
    int addedCount = 0;
    for (const QString &path : paths) {
        const QString copiedPath = copyScreenshotFileToAssets(path);
        if (copiedPath.isEmpty()) {
            continue;
        }
        screenshots.append(copiedPath);
        ++addedCount;
    }
    if (addedCount == 0) {
        QMessageBox::information(this, QStringLiteral("未添加截图"), QStringLiteral("没有可用的图片文件。"));
        return;
    }
    group.insert(QStringLiteral("screenshot_paths"), screenshots);
    group.insert(QStringLiteral("asset_dir"), ensureManualAssetDir());
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("%1 张手动截图").arg(addedCount));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    m_workflowBar->setStatusText(QStringLiteral("已添加 %1 张本地截图。").arg(addedCount));
}

void MainWindow::pasteScreenshotFromClipboard()
{
    if (m_currentGroup.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
        return;
    }

    const QString savedPath = saveClipboardImageToAssets();
    if (savedPath.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("剪贴板无截图"), QStringLiteral("剪贴板里没有可用图片。"));
        return;
    }

    QJsonObject group = currentGroupFromUi();
    QJsonArray screenshots = group.value(QStringLiteral("screenshot_paths")).toArray();
    screenshots.append(savedPath);
    group.insert(QStringLiteral("screenshot_paths"), screenshots);
    group.insert(QStringLiteral("asset_dir"), ensureManualAssetDir());
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("剪贴板截图"));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    m_workflowBar->setStatusText(QStringLiteral("已从剪贴板加入截图候选。"));
}

void MainWindow::removeScreenshotAt(int index)
{
    if (m_currentGroup.isEmpty()) {
        return;
    }
    QJsonObject group = currentGroupFromUi();
    QJsonArray screenshots = group.value(QStringLiteral("screenshot_paths")).toArray();
    if (index < 0 || index >= screenshots.size()) {
        return;
    }
    screenshots.removeAt(index);
    group.insert(QStringLiteral("screenshot_paths"), screenshots);
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), screenshots.isEmpty() ? QStringLiteral("idle") : QStringLiteral("done"),
                            screenshots.isEmpty() ? QStringLiteral("等待截图") : QStringLiteral("%1 张候选").arg(screenshots.size()));
    m_workflowBar->setStatusText(QStringLiteral("已从候选列表移除截图。"));
}

void MainWindow::generatePlaceholderScreenshots()
{
    if (m_currentGroup.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
        return;
    }

    const QString assetDir = ensureManualAssetDir();
    const QString screenshotsDir = QDir(assetDir).filePath(QStringLiteral("screenshots"));
    QDir().mkpath(screenshotsDir);

    QJsonArray screenshots;
    for (int index = 1; index <= 3; ++index) {
        const QString path = QDir(screenshotsDir).filePath(QStringLiteral("screenshot_%1.png").arg(index));
        writePlaceholderScreenshot(path, index);
        screenshots.append(path);
    }

    QJsonObject group = currentGroupFromUi();
    group.insert(QStringLiteral("asset_dir"), assetDir);
    group.insert(QStringLiteral("screenshot_paths"), screenshots);
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("3 张占位图"));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    m_workflowBar->setStatusText(QStringLiteral("已生成 3 张 1050x700 占位截图。"));
}

void MainWindow::selectOnlineApp(const QJsonObject &app)
{
    persistCurrentGroupFromUi();
    m_selectedOnlineApp = app;
    const QString appName = AppJson::stringValue(app, QStringLiteral("app_name"), app.value(QStringLiteral("pkg_name")).toString(QStringLiteral("我的应用")));
    if (m_preferredAccount.isEmpty()) {
        m_workflowBar->setStatusText(QStringLiteral("请先登录后再读取应用详情。"));
        return;
    }

    QJsonObject match;
    match.insert(QStringLiteral("app_id"), AppJson::stringValue(app, QStringLiteral("app_id"), app.value(QStringLiteral("id")).toString()));
    match.insert(QStringLiteral("detail_id"), AppJson::stringValue(app, QStringLiteral("detail_id"), app.value(QStringLiteral("id")).toString()));
    match.insert(QStringLiteral("pkg_name"), app.value(QStringLiteral("pkg_name")).toString().trimmed());
    match.insert(QStringLiteral("app_name"), appName);

    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("match"), match);
    payload.insert(QStringLiteral("fallback_name"), appName);
    runBridgeCommand(QStringLiteral("fetch_match_defaults"), payload, 300000);
}

void MainWindow::submitCurrentGroup()
{
    const QJsonObject group = currentGroupFromUi();
    if (group.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先选择我的应用，或拖入并解析包文件。"));
        return;
    }
    if (!groupHasPackages(group) && !groupTargetsExistingApp(group)) {
        QMessageBox::information(
            this,
            QStringLiteral("未选择应用"),
            QStringLiteral("请先选择一个已上架应用，或拖入本地 .deb / linglong 包。"));
        return;
    }
    if (m_preferredAccount.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未登录"), QStringLiteral("请先扫码登录或复用有效登录缓存。"));
        return;
    }

    const QString key = group.value(QStringLiteral("key")).toString();
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("groups"), QJsonArray{group});
    payload.insert(QStringLiteral("selected_keys"), QJsonArray{key});
    payload.insert(QStringLiteral("release_key"), QStringLiteral("stable"));
    payload.insert(QStringLiteral("pkg_channel"), QStringLiteral("stable"));
    payload.insert(QStringLiteral("note"), group.value(QStringLiteral("note_zh")).toString());
    runBridgeCommand(QStringLiteral("submit"), payload, 1800000);
}

void MainWindow::dragEnterEvent(QDragEnterEvent *event)
{
    if (!packagePathsFromMimeData(event->mimeData()).isEmpty()) {
        event->acceptProposedAction();
    }
}

void MainWindow::dropEvent(QDropEvent *event)
{
    const QStringList paths = packagePathsFromMimeData(event->mimeData());
    if (!paths.isEmpty()) {
        event->acceptProposedAction();
        analyzePackagePaths(paths);
    }
}

void MainWindow::analyzePackagePaths(const QStringList &paths)
{
    persistCurrentGroupFromUi();

    QStringList normalized;
    for (const QString &path : paths) {
        const QFileInfo info(path);
        if (info.isDir()) {
            normalized.append(packagePathsInDirectory(info.absoluteFilePath()));
        } else if (info.isFile() && isPackageFile(info.absoluteFilePath())) {
            normalized.append(info.absoluteFilePath());
        }
    }
    normalized.removeDuplicates();
    if (normalized.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("没有可解析的包"), QStringLiteral("请选择 .deb、.uab 或 .layer 文件。"));
        return;
    }

    QJsonArray packagePaths;
    for (const QString &path : normalized) {
        packagePaths.append(path);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("package_paths"), packagePaths);
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    runBridgeCommand(QStringLiteral("analyze"), payload, 300000);
}

QString MainWindow::ensureManualAssetDir() const
{
    const QString key = m_currentGroup.value(QStringLiteral("key")).toString(m_currentGroup.value(QStringLiteral("pkg_name")).toString(QStringLiteral("app")));
    QString root = qEnvironmentVariable("UTPUBLISHER_MANUAL_ASSET_ROOT");
    if (root.isEmpty()) {
        root = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    }
    if (root.isEmpty()) {
        root = QDir::tempPath() + QStringLiteral("/utpublisher");
    }
    const QString path = QDir(root).filePath(QStringLiteral("runtime/assets/%1").arg(AppJson::safeFileName(key)));
    QDir().mkpath(path);
    return path;
}

QString MainWindow::copyScreenshotFileToAssets(const QString &sourcePath)
{
    const QFileInfo sourceInfo(sourcePath);
    if (!sourceInfo.exists() || !sourceInfo.isFile()) {
        return {};
    }
    const QString suffix = sourceInfo.suffix().isEmpty() ? QStringLiteral("png") : sourceInfo.suffix().toLower();
    const QString screenshotsDir = QDir(ensureManualAssetDir()).filePath(QStringLiteral("screenshots"));
    QDir().mkpath(screenshotsDir);

    int index = m_currentGroup.value(QStringLiteral("screenshot_paths")).toArray().size() + 1;
    QString targetPath;
    do {
        targetPath = QDir(screenshotsDir).filePath(QStringLiteral("screenshot_%1.%2").arg(index).arg(suffix));
        ++index;
    } while (QFileInfo::exists(targetPath));

    if (QFile::copy(sourceInfo.absoluteFilePath(), targetPath)) {
        return targetPath;
    }
    return sourceInfo.absoluteFilePath();
}

QString MainWindow::saveClipboardImageToAssets()
{
    const QMimeData *mimeData = QApplication::clipboard()->mimeData();
    QImage image;
    if (mimeData->hasImage()) {
        image = qvariant_cast<QImage>(mimeData->imageData());
    }
    if (image.isNull() && mimeData->hasUrls()) {
        for (const QUrl &url : mimeData->urls()) {
            const QString path = url.toLocalFile();
            if (!path.isEmpty() && image.load(path)) {
                break;
            }
        }
    }
    if (image.isNull()) {
        return {};
    }

    const QString screenshotsDir = QDir(ensureManualAssetDir()).filePath(QStringLiteral("screenshots"));
    QDir().mkpath(screenshotsDir);
    const int index = m_currentGroup.value(QStringLiteral("screenshot_paths")).toArray().size() + 1;
    const QString path = QDir(screenshotsDir).filePath(QStringLiteral("screenshot_%1.png").arg(index));
    return image.save(path, "PNG") ? path : QString();
}

void MainWindow::writePlaceholderScreenshot(const QString &path, int index) const
{
    QImage image(1050, 700, QImage::Format_ARGB32);
    QLinearGradient background(0, 0, image.width(), image.height());
    background.setColorAt(0.0, QColor(28, 41, 58));
    background.setColorAt(0.48, QColor(62, 112 + index * 7, 154));
    background.setColorAt(1.0, QColor(232, 240, 247));

    QPainter painter(&image);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.fillRect(image.rect(), background);

    QPainterPath windowPath;
    windowPath.addRoundedRect(QRectF(150, 90, 750, 510), 18, 18);
    painter.fillPath(windowPath, QColor(255, 255, 255, 232));
    painter.setPen(QPen(QColor(255, 255, 255, 165), 2));
    painter.drawPath(windowPath);

    painter.setPen(QColor(24, 34, 48));
    QFont titleFont = painter.font();
    titleFont.setPointSize(32);
    titleFont.setBold(true);
    painter.setFont(titleFont);
    painter.drawText(QRect(210, 155, 640, 70), Qt::AlignLeft | Qt::AlignVCenter, AppJson::displayName(m_currentGroup));

    QFont normalFont = painter.font();
    normalFont.setPointSize(18);
    normalFont.setBold(false);
    painter.setFont(normalFont);
    painter.setPen(QColor(61, 73, 89));
    painter.drawText(
        QRect(210, 240, 640, 100),
        Qt::TextWordWrap,
        AppJson::stringValue(m_currentGroup, QStringLiteral("short_desc_zh"), m_currentGroup.value(QStringLiteral("short_description")).toString(QStringLiteral("应用截图占位预览"))));

    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(35, 116, 214));
    painter.drawRoundedRect(QRect(210, 365, 255, 135), 14, 14);
    painter.setBrush(QColor(236, 244, 251));
    painter.drawRoundedRect(QRect(510, 370, 280, 22), 8, 8);
    painter.drawRoundedRect(QRect(510, 412, 225, 22), 8, 8);
    painter.drawRoundedRect(QRect(510, 454, 255, 22), 8, 8);
    painter.drawRoundedRect(QRect(510, 496, 180, 22), 8, 8);

    QFont smallFont = painter.font();
    smallFont.setPointSize(14);
    smallFont.setBold(true);
    painter.setFont(smallFont);
    painter.setPen(QColor(255, 255, 255));
    painter.drawText(QRect(210, 365, 255, 135), Qt::AlignCenter, QStringLiteral("1050 x 700\nPreview %1").arg(index));
    painter.end();
    image.save(path, "PNG");
}

QStringList MainWindow::packagePathsFromMimeData(const QMimeData *mimeData)
{
    QStringList paths;
    if (mimeData == nullptr || !mimeData->hasUrls()) {
        return paths;
    }
    for (const QUrl &url : mimeData->urls()) {
        const QString path = url.toLocalFile();
        if (path.isEmpty()) {
            continue;
        }
        const QFileInfo info(path);
        if (info.isDir()) {
            paths.append(packagePathsInDirectory(info.absoluteFilePath()));
        } else if (info.isFile() && isPackageFile(info.absoluteFilePath())) {
            paths.append(info.absoluteFilePath());
        }
    }
    paths.removeDuplicates();
    return paths;
}

QString MainWindow::commandLabel(const QString &command)
{
    return commandLabelText(command);
}
