#include "MainWindow.h"

#include "core/AppJson.h"
#include "ui/AppTheme.h"
#include "ui/LoginDialog.h"
#include "ui/MetadataPanel.h"
#include "ui/QrLoginDialog.h"
#include "ui/ScreenshotPanel.h"
#include "ui/SidebarWidget.h"
#include "ui/TargetMatrixPanel.h"
#include "ui/VersionPicker.h"
#include "ui/WorkflowBar.h"

#include <DTitlebar>
#include <QApplication>
#include <QCheckBox>
#include <QClipboard>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QImage>
#include <QImageReader>
#include <QJsonArray>
#include <QLabel>
#include <QLinearGradient>
#include <QMessageBox>
#include <QMimeData>
#include <QPainter>
#include <QPainterPath>
#include <QPushButton>
#include <QScrollArea>
#include <QSizePolicy>
#include <QStackedWidget>
#include <QStandardItemModel>
#include <QStandardPaths>
#include <QStyle>
#include <QStringList>
#include <QTabWidget>
#include <QUrl>
#include <QUuid>
#include <QVBoxLayout>

#include <vector>

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
    if (command == QStringLiteral("delete_draft")) {
        return QStringLiteral("正在删除草稿...");
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
    if (command == QStringLiteral("delete_draft")) {
        return QStringLiteral("删除草稿");
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
    if (command == QStringLiteral("analyze") && compactMessage.startsWith(QStringLiteral("mixed package "))) {
        return QStringLiteral("同一组安装包需要相同的包名、版本和类型。点击「我的应用」可开始导入另一组。");
    }
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

int submissionTotalCount(const QJsonObject &report)
{
    const QJsonArray rows = report.value(QStringLiteral("rows")).toArray();
    return rows.size();
}

int submissionFailureCount(const QJsonObject &report)
{
    if (report.contains(QStringLiteral("failure_count"))) {
        return report.value(QStringLiteral("failure_count")).toInt();
    }
    int failures = 0;
    const QJsonArray rows = report.value(QStringLiteral("rows")).toArray();
    for (const QJsonValue &value : rows) {
        const QString status = value.toObject().value(QStringLiteral("status")).toString().trimmed();
        if (status != QStringLiteral("submitted")) {
            ++failures;
        }
    }
    return failures;
}

QString firstSubmissionFailureMessage(const QJsonObject &report)
{
    const QJsonArray rows = report.value(QStringLiteral("rows")).toArray();
    for (const QJsonValue &value : rows) {
        const QJsonObject row = value.toObject();
        const QString status = row.value(QStringLiteral("status")).toString().trimmed();
        if (status == QStringLiteral("submitted")) {
            continue;
        }
        const QString package = row.value(QStringLiteral("pkg_name")).toString().trimmed();
        const QString message = row.value(QStringLiteral("message")).toString().simplified();
        if (!package.isEmpty() && !message.isEmpty()) {
            return QStringLiteral("%1：%2").arg(package, message);
        }
        if (!message.isEmpty()) {
            return message;
        }
    }
    return {};
}

QJsonObject copyEditableOnlineFields(QJsonObject target, const QJsonObject &source)
{
    static const QStringList alwaysCopyKeys = {
        QStringLiteral("app_name_zh"),
        QStringLiteral("website"),
        QStringLiteral("developer_name"),
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
        QStringLiteral("draft_detail_id"),
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
        target.insert(QStringLiteral("assets_preprocessed"), false);
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

QJsonObject withoutDraftPackageRows(QJsonObject group)
{
    const QStringList removedIds = AppJson::stringArray(group.value(QStringLiteral("removed_draft_package_ids")).toArray());
    if (removedIds.isEmpty()) {
        return group;
    }
    QJsonArray packages;
    QStringList removedPaths;
    QStringList arches;
    for (const QJsonValue &value : group.value(QStringLiteral("packages")).toArray()) {
        const QJsonObject package = value.toObject();
        const QString path = package.value(QStringLiteral("path")).toString();
        if (removedIds.contains(package.value(QStringLiteral("origin_id")).toString())) {
            removedPaths.append(path);
            continue;
        }
        packages.append(value);
        const QString arch = package.value(QStringLiteral("arch")).toString();
        if (!arch.isEmpty() && !arches.contains(arch)) {
            arches.append(arch);
        }
    }
    if (removedPaths.isEmpty()) {
        return group;
    }
    QJsonArray targets;
    for (const QJsonValue &value : group.value(QStringLiteral("targets")).toArray()) {
        if (!removedPaths.contains(value.toObject().value(QStringLiteral("package_path")).toString())) {
            targets.append(value);
        }
    }
    group.insert(QStringLiteral("packages"), packages);
    group.insert(QStringLiteral("targets"), targets);
    group.insert(QStringLiteral("pkg_arches"), QJsonArray::fromStringList(arches));
    return group;
}

} // namespace

MainWindow::MainWindow(QWidget *parent)
    : Dtk::Widget::DMainWindow(parent)
    , m_bridge(new BridgeClient(QStringLiteral(APPSTORE_REPO_ROOT), this))
{
    setWindowTitle(QStringLiteral("应用投递助手"));
    setAcceptDrops(true);
    resize(1180, 800);
    setMinimumSize(980, 680);

    buildUi();
    AppTheme::apply(this);

    connect(m_bridge, &BridgeClient::commandStarted, this, &MainWindow::handleBridgeStarted);
    connect(m_bridge, &BridgeClient::commandFinished, this, &MainWindow::handleBridgeFinished);
    connect(m_bridge, &BridgeClient::commandFailed, this, &MainWindow::handleBridgeFailed);

    bootstrap();
}

void MainWindow::buildUi()
{
    titlebar()->setTitle(QStringLiteral("应用投递助手"));
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
    connect(m_sidebar, &SidebarWidget::refreshMyAppsRequested, this, [this]() {
        clearCurrentWorkspace(true, QStringLiteral("已回到我的应用列表，当前未选择应用或本地包。"));
        listMyApps();
    });
    connect(m_sidebar, &SidebarWidget::loadMoreMyAppsRequested, this, &MainWindow::loadMoreMyApps);
    connect(m_sidebar, &SidebarWidget::onlineAppSelected, this, &MainWindow::selectOnlineApp);
    connect(m_sidebar, &SidebarWidget::draftDeleteRequested, this, &MainWindow::deleteDraft);
    connect(m_sidebar, &SidebarWidget::settingsRequested, this, &MainWindow::openPreferences);
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
    m_titleLabel = new QLabel(workspace);
    m_titleLabel->setObjectName(QStringLiteral("PageTitle"));
    m_titleLabel->setTextFormat(Qt::PlainText);
    m_titleLabel->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::TextSelectableByKeyboard);
    m_titleLabel->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    header->addWidget(m_titleLabel, 1);

    m_importButton = new QPushButton(QStringLiteral("导入安装包"), workspace);
    connect(m_importButton, &QPushButton::clicked, this, &MainWindow::choosePackages);
    header->addWidget(m_importButton);
    m_syncButton = new QPushButton(QStringLiteral("同步商店"), workspace);
    m_syncButton->setToolTip(QStringLiteral("正在检查商店能力缓存"));
    connect(m_syncButton, &QPushButton::clicked, this, &MainWindow::syncStoreData);
    header->addWidget(m_syncButton);
    workspaceLayout->addLayout(header);

    m_workspacePages = new QStackedWidget(workspace);
    workspaceLayout->addWidget(m_workspacePages, 1);

    m_homePage = new QWidget(m_workspacePages);
    auto *homeLayout = new QVBoxLayout(m_homePage);
    homeLayout->setContentsMargins(24, 24, 24, 24);
    auto *dropZone = new QFrame(m_homePage);
    dropZone->setObjectName(QStringLiteral("DropZone"));
    dropZone->setMinimumWidth(320);
    auto *dropLayout = new QVBoxLayout(dropZone);
    dropLayout->setContentsMargins(36, 28, 36, 28);
    dropLayout->setSpacing(12);
    auto *dropTitle = new QLabel(QStringLiteral("导入安装包"), dropZone);
    dropTitle->setObjectName(QStringLiteral("PageTitle"));
    dropLayout->addWidget(dropTitle, 0, Qt::AlignHCenter);
    auto *hints = new QVBoxLayout();
    hints->setSpacing(2);
    for (const QString &text : {QStringLiteral("拖入 .deb、.uab 或 .layer 安装包"),
                                QStringLiteral("或从左侧打开已上架的应用")}) {
        auto *hint = new QLabel(text, dropZone);
        hint->setObjectName(QStringLiteral("MutedText"));
        hint->setAlignment(Qt::AlignCenter);
        hint->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Minimum);
        hint->setMargin(3);
        hints->addWidget(hint);
    }
    dropLayout->addLayout(hints);
    auto *chooseButton = new QPushButton(QStringLiteral("选择文件"), dropZone);
    chooseButton->setObjectName(QStringLiteral("PrimaryAction"));
    chooseButton->setCursor(Qt::PointingHandCursor);
    chooseButton->setMinimumHeight(32);
    connect(chooseButton, &QPushButton::clicked, this, &MainWindow::choosePackages);
    dropLayout->addWidget(chooseButton, 0, Qt::AlignHCenter);
    homeLayout->addStretch(1);
    homeLayout->addWidget(dropZone, 0, Qt::AlignHCenter);
    homeLayout->addStretch(1);
    m_workspacePages->addWidget(m_homePage);

    m_workspaceTabs = new QTabWidget(m_workspacePages);
    m_workspaceTabs->setObjectName(QStringLiteral("WorkspaceTabs"));
    m_workspaceTabs->setDocumentMode(true);
    m_workspacePages->addWidget(m_workspaceTabs);

    m_metadataPanel = new MetadataPanel(m_workspaceTabs);
    connect(m_metadataPanel, &MetadataPanel::packageSelected, this, &MainWindow::selectPackage);
    connect(m_metadataPanel, &MetadataPanel::packageTargetEditRequested, this, &MainWindow::editPackageTargets);
    connect(m_metadataPanel, &MetadataPanel::draftPackageRemoveRequested, this, &MainWindow::removeDraftPackage);
    m_workspaceTabs->addTab(m_metadataPanel, QStringLiteral("资料与安装包"));

    auto *assetsPage = new QWidget(m_workspaceTabs);
    auto *assetsLayout = new QVBoxLayout(assetsPage);
    assetsLayout->setContentsMargins(0, 0, 0, 0);
    assetsLayout->setSpacing(8);
    m_screenshotPanel = new ScreenshotPanel(assetsPage);
    connect(m_screenshotPanel, &ScreenshotPanel::chooseIconRequested, this, &MainWindow::chooseIconFile);
    connect(m_screenshotPanel, &ScreenshotPanel::addFilesRequested, this, &MainWindow::chooseScreenshotFiles);
    connect(m_screenshotPanel, &ScreenshotPanel::pasteRequested, this, &MainWindow::pasteScreenshotFromClipboard);
    connect(m_screenshotPanel, &ScreenshotPanel::placeholderRequested, this, &MainWindow::generatePlaceholderScreenshots);
    connect(m_screenshotPanel, &ScreenshotPanel::captureRequested, this, &MainWindow::captureScreenshots);
    connect(m_screenshotPanel, &ScreenshotPanel::preprocessRequested, this, &MainWindow::preprocessAssets);
    connect(m_screenshotPanel, &ScreenshotPanel::removeScreenshotRequested, this, &MainWindow::removeScreenshotAt);
    assetsLayout->addWidget(m_screenshotPanel, 1);
    m_preprocessAfterImportCheck = new QCheckBox(QStringLiteral("导入安装包后预处理素材"), assetsPage);
    assetsLayout->addWidget(m_preprocessAfterImportCheck);
    m_workspaceTabs->addTab(assetsPage, QStringLiteral("图标与截图"));

    m_publishPage = new QWidget(m_workspaceTabs);
    auto *publishLayout = new QVBoxLayout(m_publishPage);
    publishLayout->setContentsMargins(0, 0, 0, 0);
    auto *reviewCard = new QFrame(m_publishPage);
    reviewCard->setObjectName(QStringLiteral("Card"));
    auto *reviewLayout = new QVBoxLayout(reviewCard);
    reviewLayout->setContentsMargins(16, 14, 16, 14);
    reviewLayout->setSpacing(8);
    m_publishSummaryLabel = new QLabel(reviewCard);
    m_publishSummaryLabel->setObjectName(QStringLiteral("CardTitle"));
    m_publishSummaryLabel->setTextFormat(Qt::PlainText);
    m_publishSummaryLabel->setWordWrap(true);
    reviewLayout->addWidget(m_publishSummaryLabel);
    m_publishDetailsLabel = new QLabel(reviewCard);
    m_publishDetailsLabel->setObjectName(QStringLiteral("MutedText"));
    m_publishDetailsLabel->setWordWrap(true);
    m_publishDetailsLabel->setTextFormat(Qt::PlainText);
    reviewLayout->addWidget(m_publishDetailsLabel);
    reviewLayout->addStretch();
    auto *reviewScroll = new QScrollArea(m_publishPage);
    reviewScroll->setObjectName(QStringLiteral("FlatScroll"));
    reviewScroll->setWidgetResizable(true);
    reviewScroll->setWidget(reviewCard);
    publishLayout->addWidget(reviewScroll, 1);
    m_workspaceTabs->addTab(m_publishPage, QStringLiteral("检查与提交"));

    m_workflowBar = new WorkflowBar(workspace);
    connect(m_workflowBar, &WorkflowBar::submitRequested, this, [this]() { submitCurrentGroup(); });
    connect(m_workflowBar, &WorkflowBar::draftSaveRequested, this, [this]() { submitCurrentGroup(true); });
    workspaceLayout->addWidget(m_workflowBar);
    connect(m_workspaceTabs, &QTabWidget::currentChanged, this, [this]() {
        m_workflowBar->setPublishControlsVisible(m_workspaceTabs->currentWidget() == m_publishPage,
            !m_currentGroup.value(QStringLiteral("draft_detail_id")).toString().isEmpty());
    });
    renderCurrentGroup();
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
        m_screenshotPanel->setEnabled(false);
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
            const QString key = data.value(QStringLiteral("group_key")).toString();
            if (!key.isEmpty() && key == m_currentGroup.value(QStringLiteral("key")).toString()) {
                QJsonObject group = currentGroupFromUi();
                group.insert(QStringLiteral("targets"), data.value(QStringLiteral("targets")));
                setCurrentGroup(group);
            }
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

    if (command == QStringLiteral("save_preferences")) {
        const QJsonObject preferences = data.value(QStringLiteral("preferences")).toObject();
        if (!preferences.isEmpty()) {
            m_preferences = preferences;
        }
        m_workflowBar->setStatusText(QStringLiteral("偏好设置已保存，新解析的包会按默认系统线初始化。"));
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
        const QJsonObject project = data.value(QStringLiteral("project")).toObject();
        const QString sourceKey = project.value(QStringLiteral("source_group_key")).toString();
        if (project.contains(QStringLiteral("source_group_key")) && sourceKey != m_currentGroup.value(QStringLiteral("key")).toString()) {
            return;
        }
        const bool appended = project.value(QStringLiteral("appended")).toBool();
        if (appended) {
            persistCurrentGroupFromUi();
        }
        applySessionData(data);
        m_groups = data.value(QStringLiteral("groups")).toArray();
        if (!m_groups.isEmpty()) {
            QJsonObject group = m_groups.first().toObject();
            if (appended) {
                group = m_metadataPanel->groupFromUi(group);
                for (const QString &key : {QStringLiteral("icon_path"), QStringLiteral("screenshot_paths"),
                                           QStringLiteral("asset_dir"), QStringLiteral("assets_preprocessed")}) {
                    if (m_currentGroup.contains(key)) {
                        group.insert(key, m_currentGroup.value(key));
                    }
                }
            }
            const bool autoMatched = group.value(QStringLiteral("auto_matched_online_app")).toBool(false);
            setCurrentGroup(group);
            m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("done"), autoMatched ? QStringLiteral("自动匹配") : QStringLiteral("当前应用"));
            m_workflowBar->setStepStates(StepState::Done, StepState::Idle, StepState::Idle, StepState::Idle);
            if (appended) {
                m_workflowBar->setStatusText(QStringLiteral("已追加安装包，当前共 %1 个包。")
                    .arg(m_currentGroup.value(QStringLiteral("packages")).toArray().size()));
            } else {
                m_workflowBar->setStatusText(autoMatched
                    ? QStringLiteral("解析完成，已按包名和包类型匹配我的应用，并同步线上资料、素材与适配项。")
                    : QStringLiteral("解析完成，已载入当前应用。"));
            }
            if (m_preprocessAfterImportCheck->isChecked()) {
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
        m_screenshotPanel->setEnabled(true);
        const QJsonObject group = data.value(QStringLiteral("group")).toObject();
        if (!group.isEmpty()) {
            setCurrentGroup(group);
        }
        appendLogs(data.value(QStringLiteral("logs")).toArray());
        m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), command == QStringLiteral("capture_screenshots") ? QStringLiteral("截图已返回") : QStringLiteral("素材已准备"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
        if (command == QStringLiteral("capture_screenshots")) {
            m_workflowBar->setStatusText(QStringLiteral("截图流程完成。"));
        } else {
            const QJsonArray warnings = group.value(QStringLiteral("asset_warnings")).toArray();
            const int validCount = group.value(QStringLiteral("screenshot_paths")).toArray().size();
            const QString summary = QStringLiteral("素材已校验，保留 %1 张有效截图。").arg(validCount);
            m_workflowBar->setStatusText(warnings.isEmpty() ? summary : summary + warnings.first().toString());
        }
        return;
    }

    if (command == QStringLiteral("delete_draft")) {
        const QString detailId = data.value(QStringLiteral("detail_id")).toString();
        const QString selectedDetailId = AppJson::stringValue(m_selectedOnlineApp, QStringLiteral("detail_id"),
            m_selectedOnlineApp.value(QStringLiteral("id")).toString());
        if (!detailId.isEmpty() && (detailId == selectedDetailId
            || detailId == m_currentGroup.value(QStringLiteral("draft_detail_id")).toString())) {
            clearCurrentWorkspace(true);
        }
        listMyApps();
        return;
    }

    if (command == QStringLiteral("submit")) {
        applySessionData(data);
        appendLogs(data.value(QStringLiteral("logs")).toArray());
        const bool savedDraft = data.value(QStringLiteral("saved_draft")).toBool();
        const QJsonObject report = data.value(QStringLiteral("report")).toObject();
        const QString reportPath = report.value(QStringLiteral("report_path")).toString();
        const int totalCount = submissionTotalCount(report);
        const int failureCount = submissionFailureCount(report);
        if (failureCount > 0) {
            const QString failureMessage = firstSubmissionFailureMessage(report);
            m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("failed"), QStringLiteral("%1/%2 失败").arg(failureCount).arg(totalCount));
            m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Done, StepState::Failed);
            const QString title = savedDraft ? QStringLiteral("草稿保存失败") : QStringLiteral("提交失败");
            m_workflowBar->setStatusText(QStringLiteral("%1 %2/%3，报告：%4").arg(title).arg(failureCount).arg(totalCount).arg(reportPath));
            QMessageBox::warning(
                this,
                title,
                QStringLiteral("%1/%2 个包处理失败。\n%3\n报告：%4")
                    .arg(failureCount)
                    .arg(totalCount)
                    .arg(failureMessage)
                    .arg(reportPath));
            return;
        }
        m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("done"), QStringLiteral("当前应用"));
        m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Done, StepState::Done);
        const QString title = savedDraft ? QStringLiteral("草稿已保存") : QStringLiteral("提交完成");
        m_workflowBar->setStatusText(QStringLiteral("%1，报告：%2").arg(title, reportPath));
        const QString detailId = data.value(QStringLiteral("draft_detail_id")).toString();
        if (!detailId.isEmpty()) {
            if (detailId == m_currentGroup.value(QStringLiteral("draft_detail_id")).toString()) {
                clearCurrentWorkspace(true, title);
            }
            listMyApps();
        }
        QMessageBox::information(this, title, QStringLiteral("%1。\n报告：%2").arg(title, reportPath));
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
        m_screenshotPanel->setEnabled(true);
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

    if (shouldShowBlockingFailureDialog(command) || command == QStringLiteral("delete_draft")) {
        QMessageBox::warning(this, QStringLiteral("后端任务失败"), friendlyMessage);
    } else {
        sendMessage(style()->standardIcon(QStyle::SP_MessageBoxWarning), friendlyMessage);
    }
}

void MainWindow::applySessionData(const QJsonObject &data)
{
    const QJsonObject preferences = data.value(QStringLiteral("preferences")).toObject();
    if (!preferences.isEmpty()) {
        m_preferences = preferences;
    }

    const QJsonObject login = data.value(QStringLiteral("login")).toObject();
    const bool loggedIn = login.value(QStringLiteral("logged_in")).toBool(false);
    const QString accountLabel = login.value(QStringLiteral("account_label")).toString();
    const QString sessionAccount = login.value(QStringLiteral("session_account")).toString();
    if (!sessionAccount.isEmpty()) {
        m_preferredAccount = sessionAccount;
    } else if (!accountLabel.isEmpty()) {
        m_preferredAccount = accountLabel;
    } else if (m_preferredAccount.isEmpty() && !m_preferences.isEmpty()) {
        const QString rememberedAccount = m_preferences.value(QStringLiteral("remembered_username")).toString().trimmed();
        const QString lastSessionAccount = m_preferences.value(QStringLiteral("last_session_account")).toString().trimmed();
        m_preferredAccount = rememberedAccount.isEmpty() ? lastSessionAccount : rememberedAccount;
    }
    m_sidebar->setLoginState(loggedIn, accountLabel);

    const QJsonObject capabilities = data.value(QStringLiteral("capabilities")).toObject();
    if (data.contains(QStringLiteral("capabilities"))) {
        m_systemLineOptions = capabilities.value(QStringLiteral("default_system_line_options")).toObject();
    }
    if (capabilities.value(QStringLiteral("loaded")).toBool(false)) {
        m_syncButton->setToolTip(QStringLiteral("能力缓存：DEB 系统线 %1，玲珑系统线 %2，版本组 %3，硬件适配项 %4")
                                       .arg(capabilities.value(QStringLiteral("deb_system_line_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("linglong_system_line_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("baseline_group_count")).toInt())
                                       .arg(capabilities.value(QStringLiteral("cpu_clip_option_count")).toInt()
                                            + capabilities.value(QStringLiteral("motherboard_option_count")).toInt()));
    } else {
        m_syncButton->setToolTip(QStringLiteral("能力缓存未加载，登录后可同步商店数据"));
    }

    const QJsonArray categories = data.value(QStringLiteral("categories")).toArray();
    if (!categories.isEmpty()) {
        m_categories = categories;
    }
    m_metadataPanel->setCategories(m_categories);
}

void MainWindow::setCurrentGroup(const QJsonObject &group)
{
    const QString previousKey = m_currentGroup.value(QStringLiteral("key")).toString();
    QJsonObject nextGroup = normalizedGroup(groupWithSelectedOnlineApp(group));
    const QString draftId = nextGroup.value(QStringLiteral("draft_detail_id")).toString();
    if (!draftId.isEmpty() && draftId == m_currentGroup.value(QStringLiteral("draft_detail_id")).toString()
        && !nextGroup.contains(QStringLiteral("removed_draft_package_ids"))) {
        nextGroup.insert(QStringLiteral("removed_draft_package_ids"),
            m_currentGroup.value(QStringLiteral("removed_draft_package_ids")).toArray());
    }
    m_currentGroup = withoutDraftPackageRows(nextGroup);
    m_selectedPackagePath = effectiveSelectedPackagePath(m_currentGroup, m_selectedPackagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        m_currentGroup.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    const QString currentKey = m_currentGroup.value(QStringLiteral("key")).toString();
    if (currentKey != previousKey) {
        m_workspaceTabs->setCurrentWidget(m_metadataPanel);
    }
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

void MainWindow::clearCurrentWorkspace(bool clearOnlineSelection, const QString &statusText)
{
    m_groups = {};
    m_currentGroup = {};
    m_selectedPackagePath.clear();
    if (clearOnlineSelection) {
        m_selectedOnlineApp = {};
        m_sidebar->clearCurrentSelection();
    }
    renderCurrentGroup();
    renderSidebar();
    m_sidebar->setTaskState(QStringLiteral("parse"), QStringLiteral("idle"), QStringLiteral("等待"));
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("idle"), QStringLiteral("等待"));
    m_sidebar->setTaskState(QStringLiteral("submit"), QStringLiteral("idle"), QStringLiteral("等待"));
    m_workflowBar->setStepStates(StepState::Idle, StepState::Idle, StepState::Idle, StepState::Idle);
    if (!statusText.isEmpty()) {
        m_workflowBar->setStatusText(statusText);
    }
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
    const QString displayName = AppJson::displayName(m_currentGroup);
    m_titleLabel->setText(hasGroup ? displayName : QStringLiteral("应用管理"));
    m_titleLabel->setToolTip(hasGroup ? displayName : QString());
    m_importButton->setVisible(hasGroup);
    if (!hasGroup) {
        m_workspaceTabs->setCurrentWidget(m_metadataPanel);
    }
    m_workspacePages->setCurrentWidget(hasGroup ? static_cast<QWidget *>(m_workspaceTabs) : m_homePage);
    m_workflowBar->setPublishControlsVisible(hasGroup && m_workspaceTabs->currentWidget() == m_publishPage,
        !m_currentGroup.value(QStringLiteral("draft_detail_id")).toString().isEmpty());
    m_publishSummaryLabel->setText(QStringLiteral("%1 · %2 个安装包")
                                      .arg(displayName)
                                      .arg(m_currentGroup.value(QStringLiteral("packages")).toArray().size()));
    QStringList review{QStringLiteral("已加载 %1 张截图").arg(m_currentGroup.value(QStringLiteral("screenshot_paths")).toArray().size())};
    for (const QJsonValue &value : m_currentGroup.value(QStringLiteral("packages")).toArray()) {
        const QJsonObject package = value.toObject();
        const QStringList lines = TargetMatrixPanel::selectedSystemLines(m_currentGroup, package.value(QStringLiteral("path")).toString());
        const QString selection = lines.isEmpty() ? QStringLiteral("未选择系统线") : lines.join(QStringLiteral("、"));
        review.append(QStringLiteral("%1  ·  %2").arg(packageLabel(package), selection));
    }
    m_publishDetailsLabel->setText(review.join(QLatin1Char('\n')));
    m_selectedPackagePath = effectiveSelectedPackagePath(m_currentGroup, m_selectedPackagePath);
    m_metadataPanel->setGroup(m_currentGroup);
    m_metadataPanel->setSelectedPackagePath(m_selectedPackagePath);
    const QJsonObject packageView = groupForSelectedPackageView(m_currentGroup);
    m_screenshotPanel->setGroup(packageView);
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
    m_workflowBar->setStatusText(QStringLiteral("已选择包：%1。可在「图标与截图」查看素材。").arg(label));
}

void MainWindow::removeDraftPackage(const QString &packagePath)
{
    QJsonObject group = currentGroupFromUi();
    if (m_bridge->isBusy() || group.value(QStringLiteral("draft_detail_id")).toString().isEmpty()) {
        return;
    }
    const QJsonObject package = packageByPath(group, packagePath);
    const QString originId = package.value(QStringLiteral("origin_id")).toString();
    if (package.value(QStringLiteral("path")).toString() != packagePath || originId.isEmpty()) {
        return;
    }
    if (group.value(QStringLiteral("packages")).toArray().size() <= 1) {
        QMessageBox::information(this, QStringLiteral("草稿安装包"),
            QStringLiteral("草稿需要至少一个安装包。可从左侧应用菜单删除整份草稿。"));
        return;
    }
    QJsonArray removedIds = group.value(QStringLiteral("removed_draft_package_ids")).toArray();
    removedIds.append(originId);
    group.insert(QStringLiteral("removed_draft_package_ids"), removedIds);
    setCurrentGroup(group);
    m_workflowBar->setStatusText(QStringLiteral("已移出安装包，保存草稿后生效。"));
}

void MainWindow::editPackageTargets(const QString &packagePath)
{
    if (m_currentGroup.isEmpty() || m_bridge->isBusy()) {
        return;
    }
    QJsonObject group = currentGroupFromUi();
    const QString selectedPath = effectiveSelectedPackagePath(group, packagePath);
    if (selectedPath.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择包"), QStringLiteral("请先选择一个包。"));
        return;
    }
    if (TargetMatrixPanel::editTargets(&group, selectedPath, this)) {
        m_selectedPackagePath = selectedPath;
        group.insert(QStringLiteral("selected_package_path"), selectedPath);
        setCurrentGroup(group);
        const QString label = packageLabel(packageByPath(group, selectedPath));
        m_workflowBar->setStatusText(QStringLiteral("已更新适配范围：%1。").arg(label));
    }
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
    if (!group.contains(QStringLiteral("developer_name"))) {
        group.insert(QStringLiteral("developer_name"), group.value(QStringLiteral("dev_name")).toString());
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
    if (groupHasPackages(group) && m_currentGroup.value(QStringLiteral("online_only")).toBool(false)
        && selectedMatchId(m_currentGroup) == appId) {
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
    if (!paths.isEmpty()) {
        analyzePackagePaths(paths);
    }
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
    const bool rememberCredentials = m_preferences.value(QStringLiteral("remember_credentials")).toBool(false);
    const QString rememberedUsername = m_preferences.value(QStringLiteral("remembered_username")).toString().trimmed();
    const QString lastSessionAccount = m_preferences.value(QStringLiteral("last_session_account")).toString().trimmed();
    dialog.setRememberedCredentials(
        rememberedUsername.isEmpty() ? lastSessionAccount : rememberedUsername,
        m_preferences.value(QStringLiteral("remembered_password")).toString(),
        rememberCredentials);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    QJsonObject payload;
    if (dialog.mode() == LoginDialog::Mode::Credentials) {
        payload.insert(QStringLiteral("username"), dialog.username());
        payload.insert(QStringLiteral("password"), dialog.password());
        payload.insert(QStringLiteral("remember_credentials"), dialog.rememberCredentials());
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
    payload.insert(QStringLiteral("group"), currentGroupFromUi());
    runBridgeCommand(QStringLiteral("sync_store_data"), payload, 300000);
}

void MainWindow::openPreferences()
{
    if (m_bridge->isBusy()) {
        QMessageBox::information(this, QStringLiteral("后端忙碌"), QStringLiteral("当前后端任务尚未结束。"));
        return;
    }
    if (m_systemLineOptions.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("暂无系统线数据"), QStringLiteral("请先登录并同步商店数据。"));
        return;
    }

    struct TargetBinding {
        QString scope;
        QString code;
        QCheckBox *enabled;
        QComboBox *baseline;
        VersionPicker *excluded;
    };
    std::vector<TargetBinding> bindings;
    QJsonObject ranges = m_preferences.value(QStringLiteral("default_target_ranges")).toObject();
    const QJsonObject legacyLines = m_preferences.value(QStringLiteral("default_target_system_lines")).toObject();

    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("默认系统线和版本"));
    dialog.setAttribute(Qt::WA_StyledBackground, true);
    dialog.setStyleSheet(styleSheet());
    dialog.resize(780, 530);
    auto *layout = new QVBoxLayout(&dialog);
    auto *hint = new QLabel(QStringLiteral("为新导入的包设置默认范围。选择具体版本时会自动勾选对应系统线。"), &dialog);
    hint->setWordWrap(true);
    hint->setObjectName(QStringLiteral("MutedText"));
    layout->addWidget(hint);
    auto *formatRow = new QHBoxLayout();
    auto *formatLabel = new QLabel(QStringLiteral("包类型"), &dialog);
    auto *format = new QComboBox(&dialog);
    formatLabel->setBuddy(format);
    formatRow->addWidget(formatLabel);
    formatRow->addWidget(format);
    formatRow->addStretch();
    layout->addLayout(formatRow);

    auto *pages = new QStackedWidget(&dialog);
    for (const QString &family : {QStringLiteral("deb"), QStringLiteral("linglong")}) {
        const QJsonObject familyOptions = m_systemLineOptions.value(family).toObject();
        if (familyOptions.isEmpty()) {
            continue;
        }
        auto *tabs = new QTabWidget(pages);
        tabs->setObjectName(QStringLiteral("WorkspaceTabs"));
        pages->addWidget(tabs);
        format->addItem(family == QStringLiteral("deb") ? QStringLiteral("DEB") : QStringLiteral("玲珑包"));
        QStringList arches{QStringLiteral("x86"), QStringLiteral("arm64"), QStringLiteral("loong64"), QStringLiteral("loongarch64")};
        for (const QString &arch : familyOptions.keys()) {
            if (!arches.contains(arch)) {
                arches.append(arch);
            }
        }
        for (const QString &arch : arches) {
            const QJsonArray options = familyOptions.value(arch).toArray();
            if (options.isEmpty()) {
                continue;
            }
            const QString scope = family + QLatin1Char(':') + arch;
            QJsonObject selected = ranges.value(scope).toObject();
            if (!ranges.contains(scope)) {
                for (const QJsonValue &value : legacyLines.value(arch).toArray()) {
                    selected.insert(value.toString(), QJsonObject());
                }
                ranges.insert(scope, selected);
            }

            auto *scroll = new QScrollArea(tabs);
            scroll->setObjectName(QStringLiteral("FlatScroll"));
            scroll->setWidgetResizable(true);
            auto *host = new QWidget(scroll);
            auto *rows = new QGridLayout(host);
            rows->setHorizontalSpacing(12);
            rows->setVerticalSpacing(8);
            for (int column = 0; column < 3; ++column) {
                rows->setColumnStretch(column, 1);
            }
            rows->addWidget(new QLabel(QStringLiteral("系统线"), host), 0, 0);
            rows->addWidget(new QLabel(QStringLiteral("兼容基线"), host), 0, 1);
            rows->addWidget(new QLabel(QStringLiteral("不上架版本"), host), 0, 2);

            int row = 1;
            for (const QJsonValue &value : options) {
                const QJsonObject option = value.toObject();
                const QString code = option.value(QStringLiteral("code")).toString();
                const QString name = AppJson::stringValue(option, QStringLiteral("label"), code);
                const QJsonObject saved = selected.value(code).toObject();
                const QJsonArray versions = option.value(QStringLiteral("baseline_options")).toArray();
                auto *checkBox = new QCheckBox(name, host);
                checkBox->setChecked(selected.contains(code));
                checkBox->setToolTip(QStringLiteral("系统线 %1").arg(code));
                auto *baseline = new QComboBox(host);
                baseline->setObjectName(QStringLiteral("DefaultTargetBaseline"));
                baseline->setAccessibleName(name + QStringLiteral(" · 兼容基线"));
                baseline->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
                baseline->setMinimumContentsLength(9);
                baseline->addItem(QStringLiteral("商店默认"), QString());
                auto *model = qobject_cast<QStandardItemModel *>(baseline->model());
                const QString baselineId = saved.value(QStringLiteral("baseline_id")).toString();
                for (const QJsonValue &versionValue : versions) {
                    const QJsonObject version = versionValue.toObject();
                    const QString id = version.value(QStringLiteral("id")).toString();
                    baseline->addItem(AppJson::stringValue(version, QStringLiteral("version"), id), id);
                    const int index = baseline->count() - 1;
                    if (!version.value(QStringLiteral("selectable")).toBool(true)) {
                        if (model != nullptr) {
                            model->item(index)->setEnabled(false);
                            model->item(index)->setToolTip(QStringLiteral("这条玲珑系统线的兼容基线从 1075 开始"));
                        }
                    } else if (id == baselineId) {
                        baseline->setCurrentIndex(index);
                    }
                }
                baseline->setEnabled(!versions.isEmpty());
                auto *excluded = new VersionPicker(versions, saved.value(QStringLiteral("unsupported_baseline_ids")).toArray(),
                    name + QStringLiteral(" · 不上架版本"), QStringLiteral("无排除版本"), host);
                excluded->setEnabled(excluded->hasOptions());
                connect(baseline, qOverload<int>(&QComboBox::currentIndexChanged), checkBox, [baseline, checkBox]() {
                    if (!baseline->currentData().toString().isEmpty()) {
                        checkBox->setChecked(true);
                    }
                });
                connect(excluded, &VersionPicker::selectionChanged, checkBox, [excluded, checkBox]() {
                    if (!excluded->selected().isEmpty()) {
                        checkBox->setChecked(true);
                    }
                });
                connect(checkBox, &QCheckBox::toggled, &dialog, [baseline, excluded](bool enabled) {
                    if (!enabled) {
                        baseline->setCurrentIndex(0);
                        excluded->clearSelection();
                    }
                });
                rows->addWidget(checkBox, row, 0);
                rows->addWidget(baseline, row, 1);
                rows->addWidget(excluded, row++, 2);
                bindings.push_back(TargetBinding{scope, code, checkBox, baseline, excluded});
            }
            rows->setRowStretch(row, 1);
            scroll->setWidget(host);
            tabs->addTab(scroll, arch == QStringLiteral("x86") ? QStringLiteral("x86 / amd64") : arch);
        }
    }
    connect(format, qOverload<int>(&QComboBox::currentIndexChanged), pages, &QStackedWidget::setCurrentIndex);
    layout->addWidget(pages, 1);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }

    for (const TargetBinding &binding : bindings) {
        QJsonObject selected = ranges.value(binding.scope).toObject();
        if (binding.enabled->isChecked()) {
            selected.insert(binding.code, QJsonObject{
                {QStringLiteral("baseline_id"), binding.baseline->currentData().toString()},
                {QStringLiteral("unsupported_baseline_ids"), binding.excluded->selected()},
            });
        } else {
            selected.remove(binding.code);
        }
        ranges.insert(binding.scope, selected);
    }

    QJsonObject nextPreferences = m_preferences;
    nextPreferences.insert(QStringLiteral("default_target_ranges"), ranges);
    QJsonObject payload;
    payload.insert(QStringLiteral("preferences"), nextPreferences);
    runBridgeCommand(QStringLiteral("save_preferences"), payload, 120000);
}

void MainWindow::preprocessAssets()
{
    const QJsonObject group = currentGroupFromUi();
    if (group.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先拖入并解析包文件。"));
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

void MainWindow::chooseIconFile()
{
    if (m_currentGroup.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先选择应用或导入安装包。"));
        return;
    }
    const QString path = QFileDialog::getOpenFileName(
        this, QStringLiteral("选择应用图标"), QDir::homePath(), QStringLiteral("Images (*.png *.jpg *.jpeg *.webp *.bmp)"));
    if (path.isEmpty()) {
        return;
    }
    QImageReader reader(path);
    if (reader.read().isNull()) {
        QMessageBox::information(this, QStringLiteral("图标无法读取"), QStringLiteral("请选择一张可打开的图片。"));
        return;
    }

    QJsonObject group = currentGroupFromUi();
    group.insert(QStringLiteral("icon_path"), path);
    group.insert(QStringLiteral("assets_preprocessed"), false);
    setCurrentGroup(group);
    m_workflowBar->setStatusText(QStringLiteral("已更换应用图标。点击“预处理素材”可校验图标和截图。"));
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
    group.insert(QStringLiteral("assets_preprocessed"), false);
    group.insert(QStringLiteral("asset_dir"), ensureManualAssetDir());
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("%1 张手动截图").arg(addedCount));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    const int skippedCount = paths.size() - addedCount;
    const QString summary = QStringLiteral("已添加 %1 张本地截图。").arg(addedCount);
    m_workflowBar->setStatusText(skippedCount > 0
        ? summary + QStringLiteral("已跳过 %1 张无法读取的图片。").arg(skippedCount)
        : summary + QStringLiteral("点击“预处理素材”可调整尺寸。"));
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
    group.insert(QStringLiteral("assets_preprocessed"), false);
    group.insert(QStringLiteral("asset_dir"), ensureManualAssetDir());
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("剪贴板截图"));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    m_workflowBar->setStatusText(QStringLiteral("已从剪贴板加入截图候选，点击“预处理素材”后按商店规格调整尺寸。"));
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
    group.insert(QStringLiteral("assets_preprocessed"), false);
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

    QJsonArray screenshots;
    for (int index = 1; index <= 3; ++index) {
        const QString path = newScreenshotPath(QStringLiteral("png"));
        if (!writePlaceholderScreenshot(path, index)) {
            m_workflowBar->setStatusText(QStringLiteral("占位截图保存失败。"));
            return;
        }
        screenshots.append(path);
    }

    QJsonObject group = currentGroupFromUi();
    group.insert(QStringLiteral("asset_dir"), ensureManualAssetDir());
    group.insert(QStringLiteral("screenshot_paths"), screenshots);
    group.insert(QStringLiteral("assets_preprocessed"), false);
    setCurrentGroup(group);
    m_sidebar->setTaskState(QStringLiteral("capture"), QStringLiteral("done"), QStringLiteral("3 张占位图"));
    m_workflowBar->setStepStates(StepState::Done, StepState::Done, StepState::Running, StepState::Idle);
    m_workflowBar->setStatusText(QStringLiteral("已生成 3 张 1050x700 占位截图。"));
}

void MainWindow::selectOnlineApp(const QJsonObject &app)
{
    m_selectedOnlineApp = app;
    const QString appName = AppJson::stringValue(app, QStringLiteral("app_name"), app.value(QStringLiteral("pkg_name")).toString(QStringLiteral("我的应用")));
    clearCurrentWorkspace(false, QStringLiteral("正在切换到我的应用：%1。本地包选择已清空。").arg(appName));
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

void MainWindow::deleteDraft(const QString &detailId, const QString &appName)
{
    if (detailId.isEmpty() || m_preferredAccount.isEmpty() || m_bridge->isBusy()) {
        return;
    }
    if (QMessageBox::question(this, QStringLiteral("删除草稿"),
            QStringLiteral("确定删除「%1」的草稿？").arg(appName),
            QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) {
        return;
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("detail_id"), detailId);
    runBridgeCommand(QStringLiteral("delete_draft"), payload, 300000);
}

void MainWindow::submitCurrentGroup(bool saveDraft)
{
    const QJsonObject group = normalizedGroup(groupWithSelectedOnlineApp(currentGroupFromUi()));
    if (group.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择应用"), QStringLiteral("请先选择我的应用，或拖入并解析包文件。"));
        return;
    }
    if (saveDraft && group.value(QStringLiteral("draft_detail_id")).toString().isEmpty()) {
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

    QStringList missingSystemLines;
    for (const QJsonValue &value : group.value(QStringLiteral("packages")).toArray()) {
        const QJsonObject package = value.toObject();
        if (TargetMatrixPanel::selectedSystemLines(group, package.value(QStringLiteral("path")).toString()).isEmpty()) {
            missingSystemLines.append(packageLabel(package));
        }
    }
    if (!missingSystemLines.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("请补齐系统线"),
            QStringLiteral("以下安装包未选择系统线。请在“资料与安装包”中逐包调整。\n%1").arg(missingSystemLines.join(QLatin1Char('\n'))));
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
    payload.insert(QStringLiteral("save_draft"), saveDraft);
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
    payload.insert(QStringLiteral("current_group"), m_currentGroup);
    payload.insert(QStringLiteral("preferred_account"), m_preferredAccount);
    payload.insert(QStringLiteral("preferred_app_id"), AppJson::stringValue(
        m_selectedOnlineApp, QStringLiteral("app_id"), m_selectedOnlineApp.value(QStringLiteral("id")).toString()));
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

QString MainWindow::newScreenshotPath(const QString &suffix) const
{
    const QString screenshotsDir = QDir(ensureManualAssetDir()).filePath(QStringLiteral("screenshots"));
    QDir().mkpath(screenshotsDir);
    return QDir(screenshotsDir).filePath(QStringLiteral("screenshot-%1.%2")
        .arg(QUuid::createUuid().toString(QUuid::WithoutBraces), suffix));
}

QString MainWindow::copyScreenshotFileToAssets(const QString &sourcePath)
{
    const QFileInfo sourceInfo(sourcePath);
    if (!sourceInfo.isFile() || !QImageReader(sourcePath).canRead()) {
        return {};
    }
    const QString suffix = sourceInfo.suffix().isEmpty() ? QStringLiteral("png") : sourceInfo.suffix().toLower();
    const QString targetPath = newScreenshotPath(suffix);
    if (QFile::copy(sourceInfo.absoluteFilePath(), targetPath)) {
        return targetPath;
    }
    return sourceInfo.absoluteFilePath();
}

QString MainWindow::saveClipboardImageToAssets()
{
    const QMimeData *mimeData = QApplication::clipboard()->mimeData();
    if (mimeData == nullptr) {
        return {};
    }
    QImage image;
    if (mimeData->hasImage()) {
        image = qvariant_cast<QImage>(mimeData->imageData());
    }
    if (image.isNull() && mimeData->hasUrls()) {
        for (const QUrl &url : mimeData->urls()) {
            const QString path = url.toLocalFile();
            if (path.isEmpty()) {
                continue;
            }
            QImageReader reader(path);
            reader.setAutoTransform(true);
            image = reader.read();
            if (!image.isNull()) {
                break;
            }
        }
    }
    if (image.isNull()) {
        return {};
    }

    const QString path = newScreenshotPath(QStringLiteral("png"));
    return image.save(path, "PNG") ? path : QString();
}

bool MainWindow::writePlaceholderScreenshot(const QString &path, int index) const
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
    return image.save(path, "PNG");
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
