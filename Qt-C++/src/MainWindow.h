#pragma once

#include "BridgeClient.h"

#include <DMainWindow>
#include <QJsonArray>
#include <QJsonObject>

class QCheckBox;
class QLabel;
class QMimeData;

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
    void setCurrentGroup(const QJsonObject &group);
    void clearCurrentWorkspace(bool clearOnlineSelection, const QString &statusText = {});
    void persistCurrentGroupFromUi();
    void renderCurrentGroup();
    void renderSidebar();
    void appendLogs(const QJsonArray &logs);
    void selectPackage(const QString &packagePath);

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
    void preprocessAssets();
    void captureScreenshots();
    void chooseScreenshotFiles();
    void pasteScreenshotFromClipboard();
    void removeScreenshotAt(int index);
    void generatePlaceholderScreenshots();
    void selectOnlineApp(const QJsonObject &app);
    void submitCurrentGroup();

    QString ensureManualAssetDir() const;
    QString copyScreenshotFileToAssets(const QString &sourcePath);
    QString saveClipboardImageToAssets();
    void writePlaceholderScreenshot(const QString &path, int index) const;
    QJsonObject groupWithSelectedOnlineApp(QJsonObject group) const;

    static QStringList packagePathsFromMimeData(const QMimeData *mimeData);
    static QString commandLabel(const QString &command);

    BridgeClient *m_bridge = nullptr;
    SidebarWidget *m_sidebar = nullptr;
    MetadataPanel *m_metadataPanel = nullptr;
    ScreenshotPanel *m_screenshotPanel = nullptr;
    TargetMatrixPanel *m_targetPanel = nullptr;
    WorkflowBar *m_workflowBar = nullptr;
    QLabel *m_titleLabel = nullptr;
    QLabel *m_capabilityLabel = nullptr;
    QLabel *m_dropHintLabel = nullptr;
    QCheckBox *m_autoPilotCheck = nullptr;

    QJsonArray m_groups;
    QJsonArray m_myAppsRows;
    QJsonObject m_currentGroup;
    QJsonObject m_selectedOnlineApp;
    QJsonArray m_categories;
    QString m_preferredAccount;
    QString m_selectedPackagePath;
    QStringList m_recentLogs;
    int m_myAppsPage = 0;
    int m_myAppsTotal = 0;
    int m_myAppsPageSize = 50;
    bool m_myAppsLoadingMore = false;
};
