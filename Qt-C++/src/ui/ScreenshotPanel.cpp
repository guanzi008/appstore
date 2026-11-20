#include "ui/ScreenshotPanel.h"

#include "core/AppJson.h"
#include "ui/ImagePreview.h"

#include <QFont>
#include <QHBoxLayout>
#include <QImageIOHandler>
#include <QJsonArray>
#include <QJsonValue>
#include <QLabel>
#include <QMessageBox>
#include <QScrollArea>
#include <QScrollBar>
#include <QPushButton>
#include <QSize>
#include <QSizePolicy>
#include <QStyle>
#include <QVBoxLayout>
#include <QWidget>

namespace {

constexpr int kThumbnailHeight = 30;

QString screenshotLabel(const QString &path, int index)
{
    QImageReader reader(path);
    QSize size = reader.size();
    if (!size.isEmpty()) {
        if (reader.transformation().testFlag(QImageIOHandler::TransformationRotate90)) {
            size.transpose();
        }
        return QStringLiteral("%1  %2x%3").arg(index + 1).arg(size.width()).arg(size.height());
    }
    return QStringLiteral("%1  无效").arg(index + 1);
}

} // namespace

ScreenshotPanel::ScreenshotPanel(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("Card"));
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);
    buildUi();
}

void ScreenshotPanel::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(6);

    auto *titleRow = new QHBoxLayout();
    titleRow->setSpacing(8);
    m_iconLabel = new ImagePreview(QStringLiteral("APP"), this);
    m_iconLabel->setObjectName(QStringLiteral("PackageIconSmall"));
    m_iconLabel->setAlignment(Qt::AlignCenter);
    m_iconLabel->setFixedSize(34, 34);
    titleRow->addWidget(m_iconLabel);

    auto *titleColumn = new QVBoxLayout();
    titleColumn->setContentsMargins(0, 0, 0, 0);
    titleColumn->setSpacing(2);
    auto *title = new QLabel(QStringLiteral("当前安装包"), this);
    title->setObjectName(QStringLiteral("CardTitle"));
    m_packageLabel = new QLabel(QStringLiteral("未选择包"), this);
    m_packageLabel->setObjectName(QStringLiteral("MutedText"));
    m_packageLabel->setTextFormat(Qt::PlainText);
    m_packageLabel->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    titleColumn->addWidget(title);
    titleColumn->addWidget(m_packageLabel);
    titleRow->addLayout(titleColumn, 1);
    auto *iconButton = new QPushButton(QStringLiteral("更换图标"), this);
    iconButton->setCursor(Qt::PointingHandCursor);
    iconButton->setMinimumHeight(30);
    connect(iconButton, &QPushButton::clicked, this, &ScreenshotPanel::chooseIconRequested);
    titleRow->addWidget(iconButton);
    layout->addLayout(titleRow);

    auto *buttonRow = new QHBoxLayout();
    buttonRow->setSpacing(6);
    auto *addButton = new QPushButton(style()->standardIcon(QStyle::SP_FileDialogNewFolder), QStringLiteral("添加截图"), this);
    auto *pasteButton = new QPushButton(style()->standardIcon(QStyle::SP_DialogOpenButton), QStringLiteral("粘贴"), this);
    auto *placeholderButton = new QPushButton(style()->standardIcon(QStyle::SP_FileIcon), QStringLiteral("占位"), this);
    auto *captureButton = new QPushButton(style()->standardIcon(QStyle::SP_ComputerIcon), QStringLiteral("自动截图"), this);
    QFont captureFont = captureButton->font();
    captureFont.setStrikeOut(true);
    captureButton->setFont(captureFont);
    captureButton->setToolTip(QStringLiteral("自动截图功能目前不可用，请手动添加或粘贴截图后点击“预处理素材”。"));
    for (QPushButton *button : {addButton, pasteButton, placeholderButton, captureButton}) {
        button->setCursor(Qt::PointingHandCursor);
        button->setMinimumHeight(30);
        buttonRow->addWidget(button);
    }
    connect(addButton, &QPushButton::clicked, this, &ScreenshotPanel::addFilesRequested);
    connect(pasteButton, &QPushButton::clicked, this, &ScreenshotPanel::pasteRequested);
    connect(placeholderButton, &QPushButton::clicked, this, &ScreenshotPanel::placeholderRequested);
    connect(captureButton, &QPushButton::clicked, this, [this]() {
        QMessageBox::information(
            this,
            QStringLiteral("自动截图不可用"),
            QStringLiteral("自动截图功能目前不可用，请手动添加或粘贴截图后点击“预处理素材”。"));
    });
    layout->addLayout(buttonRow);

    m_previewLabel = new ImagePreview(QStringLiteral("等待截图"), this);
    m_previewLabel->setObjectName(QStringLiteral("ScreenshotPreview"));
    m_previewLabel->setAlignment(Qt::AlignCenter);
    m_previewLabel->setMinimumWidth(320);
    m_previewLabel->setMinimumHeight(220);
    m_previewLabel->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Ignored);
    layout->addWidget(m_previewLabel, 1);

    m_thumbScroll = new QScrollArea(this);
    m_thumbScroll->setObjectName(QStringLiteral("FlatScroll"));
    m_thumbScroll->setWidgetResizable(true);
    m_thumbScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    m_thumbScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_thumbScroll->setFixedHeight(kThumbnailHeight + m_thumbScroll->horizontalScrollBar()->sizeHint().height());
    auto *thumbHost = new QWidget(m_thumbScroll);
    m_thumbLayout = new QHBoxLayout();
    m_thumbLayout->setContentsMargins(0, 0, 0, 0);
    m_thumbLayout->setSpacing(5);
    thumbHost->setLayout(m_thumbLayout);
    m_thumbScroll->setWidget(thumbHost);
    layout->addWidget(m_thumbScroll);

    auto *footerRow = new QHBoxLayout();
    footerRow->setSpacing(6);
    m_metaLabel = new QLabel(QStringLiteral("未检测到有效截图"), this);
    m_metaLabel->setObjectName(QStringLiteral("MutedText"));
    footerRow->addWidget(m_metaLabel, 1);
    m_removeButton = new QPushButton(style()->standardIcon(QStyle::SP_TrashIcon), QStringLiteral("删除所选"), this);
    m_removeButton->setCursor(Qt::PointingHandCursor);
    m_removeButton->setEnabled(false);
    m_removeButton->setMinimumHeight(30);
    connect(m_removeButton, &QPushButton::clicked, this, [this]() {
        if (m_selectedIndex < 0 || m_selectedIndex >= m_screenshotPaths.size()) {
            return;
        }
        emit removeScreenshotRequested(m_selectedIndex);
    });
    footerRow->addWidget(m_removeButton);
    auto *preprocessButton = new QPushButton(style()->standardIcon(QStyle::SP_DialogApplyButton), QStringLiteral("预处理素材"), this);
    preprocessButton->setCursor(Qt::PointingHandCursor);
    preprocessButton->setMinimumHeight(30);
    connect(preprocessButton, &QPushButton::clicked, this, &ScreenshotPanel::preprocessRequested);
    footerRow->addWidget(preprocessButton);
    layout->addLayout(footerRow);
}

void ScreenshotPanel::setGroup(const QJsonObject &group)
{
    const QString packageLabel = AppJson::stringValue(
        group,
        QStringLiteral("selected_package_label"),
        AppJson::stringValue(group, QStringLiteral("pkg_name"), QStringLiteral("未选择包")));
    m_packageLabel->setText(packageLabel);
    m_packageLabel->setToolTip(packageLabel);
    m_iconLabel->setImagePath(group.value(QStringLiteral("icon_path")).toString().trimmed(), QStringLiteral("APP"));
    QStringList warnings;
    for (const QJsonValue &value : group.value(QStringLiteral("asset_warnings")).toArray()) {
        warnings.append(value.toString());
    }
    m_metaLabel->setToolTip(warnings.join(QLatin1Char('\n')));

    m_screenshotPaths.clear();
    const QJsonArray screenshots = group.value(QStringLiteral("screenshot_paths")).toArray();
    for (const QJsonValue &value : screenshots) {
        const QString path = value.toString().trimmed();
        if (!path.isEmpty()) {
            m_screenshotPaths.append(path);
        }
    }
    if (m_screenshotPaths.isEmpty()) {
        m_selectedIndex = -1;
    } else if (m_selectedIndex < 0 || m_selectedIndex >= m_screenshotPaths.size()) {
        m_selectedIndex = 0;
    }
    renderScreenshotList();
    updatePreview();
}

void ScreenshotPanel::renderScreenshotList()
{
    while (QLayoutItem *item = m_thumbLayout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->hide();
            widget->deleteLater();
        }
        delete item;
    }

    if (m_screenshotPaths.isEmpty()) {
        if (m_thumbScroll != nullptr) {
            m_thumbScroll->setVisible(false);
        }
        return;
    }

    if (m_thumbScroll != nullptr) {
        m_thumbScroll->setVisible(true);
    }
    for (int index = 0; index < m_screenshotPaths.size(); ++index) {
        auto *button = new QPushButton(screenshotLabel(m_screenshotPaths.at(index), index), this);
        button->setProperty("class", QStringLiteral("ScreenshotThumb"));
        button->setCheckable(true);
        button->setAutoExclusive(true);
        button->setChecked(index == m_selectedIndex);
        button->setCursor(Qt::PointingHandCursor);
        button->setFixedHeight(kThumbnailHeight);
        button->setToolTip(m_screenshotPaths.at(index));
        connect(button, &QPushButton::clicked, this, [this, index]() {
            selectScreenshot(index);
        });
        m_thumbLayout->addWidget(button);
    }
    m_thumbLayout->addStretch(1);
}

void ScreenshotPanel::selectScreenshot(int index)
{
    if (index < 0 || index >= m_screenshotPaths.size()) {
        return;
    }
    m_selectedIndex = index;
    updatePreview();
}

void ScreenshotPanel::updatePreview()
{
    const bool hasSelection = m_selectedIndex >= 0 && m_selectedIndex < m_screenshotPaths.size();
    const QString screenshotPath = hasSelection ? m_screenshotPaths.at(m_selectedIndex) : QString();
    const QSize imageSize = m_previewLabel->setImagePath(
        screenshotPath, hasSelection ? QStringLiteral("无法预览这张截图") : QStringLiteral("等待截图"));
    m_removeButton->setEnabled(hasSelection);
    if (!imageSize.isEmpty()) {
        m_metaLabel->setText(QStringLiteral("第 %1 张：%2x%3（共 %4 张）")
                                 .arg(m_selectedIndex + 1)
                                 .arg(imageSize.width())
                                 .arg(imageSize.height())
                                 .arg(m_screenshotPaths.size()));
        return;
    }

    m_metaLabel->setText(m_screenshotPaths.isEmpty() ? QStringLiteral("未检测到有效截图") : QStringLiteral("所选截图无效"));
}
