#include "ui/ScreenshotPanel.h"

#include "core/AppJson.h"

#include <QHBoxLayout>
#include <QImage>
#include <QJsonArray>
#include <QJsonValue>
#include <QLabel>
#include <QPixmap>
#include <QScrollArea>
#include <QPushButton>
#include <QSize>
#include <QSizePolicy>
#include <QStyle>
#include <QVBoxLayout>
#include <QWidget>

namespace {

QString screenshotLabel(const QString &path, int index)
{
    const QImage image(path);
    if (!image.isNull()) {
        return QStringLiteral("%1  %2x%3").arg(index + 1).arg(image.width()).arg(image.height());
    }
    return QStringLiteral("%1  无效").arg(index + 1);
}

} // namespace

ScreenshotPanel::ScreenshotPanel(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("Card"));
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Maximum);
    buildUi();
}

void ScreenshotPanel::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(6);

    auto *titleRow = new QHBoxLayout();
    titleRow->setSpacing(8);
    m_iconLabel = new QLabel(QStringLiteral("APP"), this);
    m_iconLabel->setObjectName(QStringLiteral("PackageIconSmall"));
    m_iconLabel->setAlignment(Qt::AlignCenter);
    m_iconLabel->setFixedSize(34, 34);
    titleRow->addWidget(m_iconLabel);

    auto *titleColumn = new QVBoxLayout();
    titleColumn->setContentsMargins(0, 0, 0, 0);
    titleColumn->setSpacing(2);
    auto *title = new QLabel(QStringLiteral("截图工坊（1050x700 规格预检）"), this);
    title->setObjectName(QStringLiteral("CardTitle"));
    m_packageLabel = new QLabel(QStringLiteral("未选择包"), this);
    m_packageLabel->setObjectName(QStringLiteral("MutedText"));
    titleColumn->addWidget(title);
    titleColumn->addWidget(m_packageLabel);
    titleRow->addLayout(titleColumn, 1);
    layout->addLayout(titleRow);

    auto *buttonRow = new QHBoxLayout();
    buttonRow->setSpacing(6);
    auto *addButton = new QPushButton(style()->standardIcon(QStyle::SP_FileDialogNewFolder), QStringLiteral("添加截图"), this);
    auto *pasteButton = new QPushButton(style()->standardIcon(QStyle::SP_DialogOpenButton), QStringLiteral("粘贴"), this);
    auto *placeholderButton = new QPushButton(style()->standardIcon(QStyle::SP_FileIcon), QStringLiteral("占位"), this);
    auto *captureButton = new QPushButton(style()->standardIcon(QStyle::SP_ComputerIcon), QStringLiteral("自动截图"), this);
    for (QPushButton *button : {addButton, pasteButton, placeholderButton, captureButton}) {
        button->setCursor(Qt::PointingHandCursor);
        button->setMinimumHeight(30);
        buttonRow->addWidget(button);
    }
    connect(addButton, &QPushButton::clicked, this, &ScreenshotPanel::addFilesRequested);
    connect(pasteButton, &QPushButton::clicked, this, &ScreenshotPanel::pasteRequested);
    connect(placeholderButton, &QPushButton::clicked, this, &ScreenshotPanel::placeholderRequested);
    connect(captureButton, &QPushButton::clicked, this, &ScreenshotPanel::captureRequested);
    layout->addLayout(buttonRow);

    m_previewLabel = new QLabel(QStringLiteral("等待截图"), this);
    m_previewLabel->setObjectName(QStringLiteral("ScreenshotPreview"));
    m_previewLabel->setAlignment(Qt::AlignCenter);
    m_previewLabel->setMinimumWidth(320);
    m_previewLabel->setFixedHeight(146);
    layout->addWidget(m_previewLabel);

    m_thumbScroll = new QScrollArea(this);
    m_thumbScroll->setObjectName(QStringLiteral("FlatScroll"));
    m_thumbScroll->setWidgetResizable(true);
    m_thumbScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    m_thumbScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_thumbScroll->setFixedHeight(34);
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
    const QString iconPath = group.value(QStringLiteral("selected_package_icon_path")).toString(
        group.value(QStringLiteral("icon_path")).toString());
    const QPixmap icon(iconPath);
    if (!icon.isNull()) {
        m_iconLabel->setPixmap(icon.scaled(m_iconLabel->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
        m_iconLabel->setText({});
    } else {
        m_iconLabel->setPixmap({});
        m_iconLabel->setText(QStringLiteral("APP"));
    }

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
        button->setChecked(index == m_selectedIndex);
        button->setCursor(Qt::PointingHandCursor);
        button->setMinimumHeight(28);
        button->setMaximumWidth(118);
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
    renderScreenshotList();
    updatePreview();
}

void ScreenshotPanel::updatePreview()
{
    const QString screenshotPath = (m_selectedIndex >= 0 && m_selectedIndex < m_screenshotPaths.size())
        ? m_screenshotPaths.at(m_selectedIndex)
        : QString();
    const QPixmap pixmap(screenshotPath);
    if (!screenshotPath.isEmpty() && !pixmap.isNull()) {
        m_previewLabel->setPixmap(pixmap.scaled(m_previewLabel->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
        m_previewLabel->setText({});
        const QImage image(screenshotPath);
        m_metaLabel->setText(QStringLiteral("第 %1 张：%2x%3（共 %4 张）")
                                 .arg(m_selectedIndex + 1)
                                 .arg(image.width())
                                 .arg(image.height())
                                 .arg(m_screenshotPaths.size()));
        m_removeButton->setEnabled(true);
        return;
    }

    m_previewLabel->setPixmap({});
    m_previewLabel->setText(QStringLiteral("等待截图"));
    m_metaLabel->setText(m_screenshotPaths.isEmpty() ? QStringLiteral("未检测到有效截图") : QStringLiteral("所选截图无效"));
    m_removeButton->setEnabled(false);
}
