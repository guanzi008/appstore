#pragma once

#include "BridgeClient.h"

#include <DMainWindow>
#include <QJsonArray>
#include <QJsonObject>

class QCheckBox;
class QLabel;
class QMimeData;
class QPushButton;
class QStackedWidget;
class QTabWidget;

class MetadataPanel;
class ScreenshotPanel;
class SidebarWidget;
class TargetMatrixPanel;
class WorkflowBar;

class MainWindow final : public Dtk::Widget::DMainWindow
{
    Q_OBJECT
    Q_DISABLE_COPY(MainWindow)

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

protected:
    void dragEnterEvent(QDragEnterEvent *event) override;
    void dropEvent(QDropEvent *event) override;

private:
    void buildUi();
    void bootstrap();
    void analyzePackagePaths(const QStringList &paths);
    void runBridgeCommand(const QString &command, const QJsonObject &payload, int timeoutMs = 600000);

    void handleBridgeStarted(const QString &command);
    void handleBridgeFinished(const QString &command, const QJsonObject &data);
    void handleBridgeFailed(const QString &command, const QString &message, const QString &traceback);

    void applySessionData(const QJsonObject &data);
    void applyPreferences(const QJsonObject &preferences);
    void setCurrentGroup(const QJsonObject &group);
    void clearCurrentWorkspace(bool clearOnlineSelection, const QString &statusText = {});
    void persistCurrentGroupFromUi();
    void renderCurrentGroup();
    void renderSidebar();
    void appendLogs(const QJsonArray &logs);
    void selectPackage(const QString &packagePath);
    void editPackageTargets(const QString &packagePath);
    void removeDraftPackage(const QString &packagePath);

    QJsonObject normalizedGroup(QJsonObject group) const;
    QJsonObject currentGroupFromUi() const;
    QJsonObject groupForSelectedPackageView(const QJsonObject &group) const;
    QJsonObject groupByKey(const QString &key) const;

    void choosePackages();
    void listMyApps();
    void loadMoreMyApps();
    void loginByWechat();
    void logout();
    void syncStoreData();
    void openPreferences();
    void openMcpPreferences();
    void preprocessAssets();
    void captureScreenshots();
    void chooseIconFile();
    void chooseScreenshotFiles();
    void pasteScreenshotFromClipboard();
    void removeScreenshotAt(int index);
    void generatePlaceholderScreenshots();
    void selectOnlineApp(const QJsonObject &app);
    void deleteDraft(const QString &detailId, const QString &appName);
    void submitCurrentGroup(bool saveDraft = false);

    QString ensureManualAssetDir() const;
    QString newScreenshotPath(const QString &suffix) const;
    QString copyScreenshotFileToAssets(const QString &sourcePath);
    QString saveClipboardImageToAssets();
    bool writePlaceholderScreenshot(const QString &path, int index) const;
    QJsonObject groupWithSelectedOnlineApp(QJsonObject group) const;

    static QStringList packagePathsFromMimeData(const QMimeData *mimeData);
    static QString commandLabel(const QString &command);

    BridgeClient *m_bridge = nullptr;
    QProcess m_mcpHttpProcess;
    QProcess m_mcpSseProcess;
    SidebarWidget *m_sidebar = nullptr;
    MetadataPanel *m_metadataPanel = nullptr;
    ScreenshotPanel *m_screenshotPanel = nullptr;
    WorkflowBar *m_workflowBar = nullptr;
    QLabel *m_titleLabel = nullptr;
    QLabel *m_publishSummaryLabel = nullptr;
    QLabel *m_publishDetailsLabel = nullptr;
    QPushButton *m_importButton = nullptr;
    QPushButton *m_syncButton = nullptr;
    QStackedWidget *m_workspacePages = nullptr;
    QWidget *m_homePage = nullptr;
    QTabWidget *m_workspaceTabs = nullptr;
    QWidget *m_publishPage = nullptr;
    QCheckBox *m_preprocessAfterImportCheck = nullptr;

    QJsonArray m_groups;
    QJsonArray m_myAppsRows;
    QJsonObject m_currentGroup;
    QJsonObject m_selectedOnlineApp;
    QJsonObject m_preferences;
    QJsonObject m_systemLineOptions;
    QJsonArray m_categories;
    QString m_preferredAccount;
    QString m_selectedPackagePath;
    QStringList m_recentLogs;
    int m_myAppsPage = 0;
    int m_myAppsTotal = 0;
    int m_myAppsPageSize = 50;
    bool m_myAppsLoadingMore = false;
};
