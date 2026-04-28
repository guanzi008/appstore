#pragma once

#include <QFrame>
#include <QJsonObject>
#include <QStringList>

class QHBoxLayout;
class QLabel;
class QPushButton;
class QScrollArea;

class ScreenshotPanel final : public QFrame
{
    Q_OBJECT
    Q_DISABLE_COPY(ScreenshotPanel)

public:
    explicit ScreenshotPanel(QWidget *parent = nullptr);

    void setGroup(const QJsonObject &group);

signals:
    void addFilesRequested();
    void pasteRequested();
    void placeholderRequested();
    void captureRequested();
    void preprocessRequested();
    void removeScreenshotRequested(int index);

private:
    void buildUi();
    void renderScreenshotList();
    void selectScreenshot(int index);
    void updatePreview();

    QLabel *m_iconLabel = nullptr;
    QLabel *m_packageLabel = nullptr;
    QLabel *m_previewLabel = nullptr;
    QLabel *m_metaLabel = nullptr;
    QScrollArea *m_thumbScroll = nullptr;
    QHBoxLayout *m_thumbLayout = nullptr;
    QPushButton *m_removeButton = nullptr;
    QStringList m_screenshotPaths;
    int m_selectedIndex = -1;
};
