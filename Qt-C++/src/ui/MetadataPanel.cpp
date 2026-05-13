#include "ui/MetadataPanel.h"

#include "core/AppJson.h"

#include <QCheckBox>
#include <QComboBox>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QLayoutItem>
#include <QPixmap>
#include <QPushButton>
#include <QScrollArea>
#include <QSizePolicy>
#include <QTabWidget>
#include <QTextEdit>
#include <QVBoxLayout>
#include <QWidget>

namespace {

constexpr int kControlHeight = 28;
constexpr int kLabelColumnWidth = 66;
constexpr int kCategoryColumnWidth = 132;
constexpr int kLanguageDescriptionHeight = 56;
constexpr int kLanguageNoteHeight = 38;

void configureLineEdit(QLineEdit *edit)
{
    edit->setMinimumHeight(kControlHeight);
    edit->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
}

void configureComboBox(QComboBox *comboBox)
{
    comboBox->setMinimumSize(kCategoryColumnWidth, kControlHeight);
    comboBox->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
}

void configureTextEdit(QTextEdit *edit, int height)
{
    edit->setFixedHeight(height);
    edit->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    edit->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    edit->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
}

void configureRegionCheck(QCheckBox *checkBox)
{
    checkBox->setMinimumHeight(kControlHeight);
    checkBox->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
}

QString packageTitle(const QJsonObject &package)
{
    const QString fileName = package.value(QStringLiteral("file_name")).toString().trimmed();
    const QString pkgName = package.value(QStringLiteral("pkg_name")).toString().trimmed();
    const QString version = package.value(QStringLiteral("version")).toString().trimmed();
    const QString arch = package.value(QStringLiteral("arch")).toString().trimmed();
    const QString family = package.value(QStringLiteral("family")).toString().trimmed();
    const QString status = package.value(QStringLiteral("status_text")).toString().trimmed();
    const QString size = package.value(QStringLiteral("size_text")).toString().trimmed();
    const QString systemText = package.value(QStringLiteral("system_text")).toString().trimmed();

    const QString title = pkgName.isEmpty() ? fileName : pkgName;
    if (title.isEmpty()) {
        return QStringLiteral("未命名包");
    }
    QStringList parts;
    if (!version.isEmpty()) {
        parts.append(version);
    }
    if (!arch.isEmpty()) {
        parts.append(arch);
    }
    if (!family.isEmpty()) {
        parts.append(family);
    }
    if (!status.isEmpty()) {
        parts.append(status);
    }
    if (!size.isEmpty()) {
        parts.append(size);
    }
    if (!systemText.isEmpty()) {
        parts.append(systemText);
    }
    return parts.isEmpty() ? title : QStringLiteral("%1  ·  %2").arg(title, parts.join(QStringLiteral(" / ")));
}

QJsonObject packageForPath(const QJsonObject &group, const QString &packagePath)
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

QString effectivePackagePath(const QJsonObject &group, const QString &packagePath)
{
    const QJsonObject direct = packageForPath(group, packagePath);
    if (!packagePath.trimmed().isEmpty() && direct.value(QStringLiteral("path")).toString() == packagePath) {
        return packagePath;
    }
    const QString fromGroup = group.value(QStringLiteral("selected_package_path")).toString().trimmed();
    const QJsonObject selected = packageForPath(group, fromGroup);
    if (!fromGroup.isEmpty() && selected.value(QStringLiteral("path")).toString() == fromGroup) {
        return fromGroup;
    }
    return packageForPath(group, {}).value(QStringLiteral("path")).toString();
}

} // namespace

MetadataPanel::MetadataPanel(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("Card"));
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);
    buildUi();
}

void MetadataPanel::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(5);

    auto *title = new QLabel(QStringLiteral("应用资料与包列表（自动填充）"), this);
    title->setObjectName(QStringLiteral("CardTitle"));
    layout->addWidget(title);

    auto *summaryRow = new QHBoxLayout();
    summaryRow->setSpacing(12);
    m_iconLabel = new QLabel(QStringLiteral("APP"), this);
    m_iconLabel->setObjectName(QStringLiteral("PackageIcon"));
    m_iconLabel->setAlignment(Qt::AlignCenter);
    m_iconLabel->setFixedSize(50, 50);
    summaryRow->addWidget(m_iconLabel);

    auto *summaryGrid = new QGridLayout();
    summaryGrid->setHorizontalSpacing(22);
    summaryGrid->setVerticalSpacing(2);
    summaryGrid->addWidget(makeCaption(QStringLiteral("应用名："), this), 0, 0);
    summaryGrid->addWidget(makeCaption(QStringLiteral("版本号："), this), 0, 1);
    summaryGrid->addWidget(makeCaption(QStringLiteral("架构："), this), 0, 2);
    summaryGrid->addWidget(makeCaption(QStringLiteral("类型："), this), 0, 3);
    m_pkgNameLabel = new QLabel(QStringLiteral("-"), this);
    m_versionLabel = new QLabel(QStringLiteral("-"), this);
    m_archLabel = new QLabel(QStringLiteral("-"), this);
    m_familyLabel = new QLabel(QStringLiteral("-"), this);
    for (QLabel *label : {m_pkgNameLabel, m_versionLabel, m_archLabel, m_familyLabel}) {
        label->setObjectName(QStringLiteral("SummaryValue"));
    }
    summaryGrid->addWidget(m_pkgNameLabel, 1, 0);
    summaryGrid->addWidget(m_versionLabel, 1, 1);
    summaryGrid->addWidget(m_archLabel, 1, 2);
    summaryGrid->addWidget(m_familyLabel, 1, 3);
    summaryRow->addLayout(summaryGrid, 1);
    layout->addLayout(summaryRow);

    auto *packageHeader = new QHBoxLayout();
    packageHeader->setContentsMargins(0, 0, 0, 0);
    packageHeader->addWidget(makeCaption(QStringLiteral("包列表"), this));
    auto *packageHint = new QLabel(QStringLiteral("点击包后右侧显示对应图标、截图与适配范围"), this);
    packageHint->setObjectName(QStringLiteral("MutedText"));
    packageHeader->addWidget(packageHint, 1);
    layout->addLayout(packageHeader);

    m_packageScroll = new QScrollArea(this);
    m_packageScroll->setObjectName(QStringLiteral("FlatScroll"));
    m_packageScroll->setWidgetResizable(true);
    m_packageScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_packageScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    m_packageScroll->setMinimumHeight(34);
    m_packageScroll->setMaximumHeight(38);
    auto *packageHost = new QWidget(m_packageScroll);
    m_packageLayout = new QVBoxLayout(packageHost);
    m_packageLayout->setContentsMargins(0, 0, 4, 0);
    m_packageLayout->setSpacing(4);
    m_packageScroll->setWidget(packageHost);
    layout->addWidget(m_packageScroll);

    auto *divider = new QFrame(this);
    divider->setFrameShape(QFrame::HLine);
    divider->setStyleSheet(QStringLiteral("color: rgba(180,195,210,120);"));
    layout->addWidget(divider);

    auto *formGrid = new QGridLayout();
    formGrid->setContentsMargins(0, 0, 0, 0);
    formGrid->setHorizontalSpacing(8);
    formGrid->setVerticalSpacing(4);
    formGrid->setColumnMinimumWidth(0, kLabelColumnWidth);
    formGrid->setColumnMinimumWidth(2, 46);
    formGrid->setColumnMinimumWidth(3, kCategoryColumnWidth);
    formGrid->setColumnStretch(1, 1);
    formGrid->setRowMinimumHeight(0, kControlHeight);
    formGrid->setRowMinimumHeight(1, kControlHeight);
    m_categoryCombo = new QComboBox(this);
    m_websiteEdit = new QLineEdit(this);
    m_websiteEdit->setPlaceholderText(QStringLiteral("官网或项目地址"));
    m_developerNameEdit = new QLineEdit(this);
    m_developerNameEdit->setPlaceholderText(QStringLiteral("开发者名称"));
    configureLineEdit(m_websiteEdit);
    configureLineEdit(m_developerNameEdit);
    configureComboBox(m_categoryCombo);

    formGrid->addWidget(makeCaption(QStringLiteral("官网"), this), 0, 0);
    formGrid->addWidget(m_websiteEdit, 0, 1);
    formGrid->addWidget(makeCaption(QStringLiteral("分类"), this), 0, 2);
    formGrid->addWidget(m_categoryCombo, 0, 3);
    formGrid->addWidget(makeCaption(QStringLiteral("开发者"), this), 1, 0);
    formGrid->addWidget(m_developerNameEdit, 1, 1, 1, 3);

    m_languageTabs = new QTabWidget(this);
    m_languageTabs->setObjectName(QStringLiteral("LanguageTabs"));
    m_languageTabs->setDocumentMode(true);
    m_languageTabs->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    m_languageTabs->setMinimumHeight(238);
    m_languageTabs->setMaximumHeight(250);

    auto *zhPage = new QWidget(m_languageTabs);
    auto *zhGrid = new QGridLayout(zhPage);
    zhGrid->setContentsMargins(0, 6, 0, 0);
    zhGrid->setHorizontalSpacing(8);
    zhGrid->setVerticalSpacing(4);
    zhGrid->setColumnMinimumWidth(0, kLabelColumnWidth);
    zhGrid->setColumnStretch(1, 1);
    m_appNameEdit = new QLineEdit(zhPage);
    m_appNameEdit->setPlaceholderText(QStringLiteral("应用中文名称"));
    m_shortDescEdit = new QLineEdit(zhPage);
    m_shortDescEdit->setPlaceholderText(QStringLiteral("一句话介绍"));
    m_fullDescEdit = new QTextEdit(zhPage);
    m_fullDescEdit->setObjectName(QStringLiteral("LargeText"));
    m_fullDescEdit->setPlaceholderText(QStringLiteral("详情描述"));
    m_noteEdit = new QTextEdit(zhPage);
    m_noteEdit->setPlaceholderText(QStringLiteral("更新说明"));
    configureLineEdit(m_appNameEdit);
    configureLineEdit(m_shortDescEdit);
    configureTextEdit(m_fullDescEdit, kLanguageDescriptionHeight);
    configureTextEdit(m_noteEdit, kLanguageNoteHeight);
    zhGrid->addWidget(makeCaption(QStringLiteral("名称"), zhPage), 0, 0);
    zhGrid->addWidget(m_appNameEdit, 0, 1);
    zhGrid->addWidget(makeCaption(QStringLiteral("简介"), zhPage), 1, 0);
    zhGrid->addWidget(m_shortDescEdit, 1, 1);
    zhGrid->addWidget(makeCaption(QStringLiteral("详情"), zhPage), 2, 0);
    zhGrid->addWidget(m_fullDescEdit, 2, 1);
    zhGrid->addWidget(makeCaption(QStringLiteral("更新"), zhPage), 3, 0);
    zhGrid->addWidget(m_noteEdit, 3, 1);
    m_languageTabs->addTab(zhPage, QStringLiteral("中文文案"));

    auto *enPage = new QWidget(m_languageTabs);
    auto *enGrid = new QGridLayout(enPage);
    enGrid->setContentsMargins(0, 6, 0, 0);
    enGrid->setHorizontalSpacing(8);
    enGrid->setVerticalSpacing(4);
    enGrid->setColumnMinimumWidth(0, kLabelColumnWidth);
    enGrid->setColumnStretch(1, 1);
    m_appNameEnEdit = new QLineEdit(enPage);
    m_appNameEnEdit->setPlaceholderText(QStringLiteral("English app name"));
    m_shortDescEnEdit = new QLineEdit(enPage);
    m_shortDescEnEdit->setPlaceholderText(QStringLiteral("English short description"));
    m_fullDescEnEdit = new QTextEdit(enPage);
    m_fullDescEnEdit->setObjectName(QStringLiteral("LargeText"));
    m_fullDescEnEdit->setPlaceholderText(QStringLiteral("English full description"));
    m_noteEnEdit = new QTextEdit(enPage);
    m_noteEnEdit->setPlaceholderText(QStringLiteral("English release note"));
    configureLineEdit(m_appNameEnEdit);
    configureLineEdit(m_shortDescEnEdit);
    configureTextEdit(m_fullDescEnEdit, kLanguageDescriptionHeight);
    configureTextEdit(m_noteEnEdit, kLanguageNoteHeight);
    enGrid->addWidget(makeCaption(QStringLiteral("Name"), enPage), 0, 0);
    enGrid->addWidget(m_appNameEnEdit, 0, 1);
    enGrid->addWidget(makeCaption(QStringLiteral("Brief"), enPage), 1, 0);
    enGrid->addWidget(m_shortDescEnEdit, 1, 1);
    enGrid->addWidget(makeCaption(QStringLiteral("Detail"), enPage), 2, 0);
    enGrid->addWidget(m_fullDescEnEdit, 2, 1);
    enGrid->addWidget(makeCaption(QStringLiteral("Note"), enPage), 3, 0);
    enGrid->addWidget(m_noteEnEdit, 3, 1);
    m_languageTabs->addTab(enPage, QStringLiteral("English Copy"));
    formGrid->addWidget(m_languageTabs, 2, 0, 1, 4);

    auto *regionHost = new QWidget(this);
    regionHost->setObjectName(QStringLiteral("InlineControls"));
    auto *regionRow = new QHBoxLayout(regionHost);
    regionRow->setContentsMargins(0, 0, 0, 0);
    regionRow->setSpacing(12);
    m_regionChina = new QCheckBox(QStringLiteral("中国（包含港澳台）"), this);
    m_regionChina->setChecked(true);
    m_regionGlobal = new QCheckBox(QStringLiteral("其他地区"), this);
    m_replaceAssetsCheck = new QCheckBox(QStringLiteral("替换图标/截图"), this);
    configureRegionCheck(m_regionChina);
    configureRegionCheck(m_regionGlobal);
    configureRegionCheck(m_replaceAssetsCheck);
    regionRow->addWidget(m_regionChina);
    regionRow->addWidget(m_regionGlobal);
    regionRow->addStretch(1);
    regionRow->addWidget(m_replaceAssetsCheck);
    layout->addLayout(formGrid);

    auto *regionLine = new QWidget(this);
    auto *regionLineLayout = new QHBoxLayout(regionLine);
    regionLineLayout->setContentsMargins(0, 0, 0, 0);
    regionLineLayout->setSpacing(8);
    auto *regionCaption = makeCaption(QStringLiteral("发布区域"), regionLine);
    regionCaption->setFixedWidth(kLabelColumnWidth);
    regionLineLayout->addWidget(regionCaption);
    regionLineLayout->addWidget(regionHost, 1);
    layout->addWidget(regionLine);
}

void MetadataPanel::setCategories(const QJsonArray &categories)
{
    m_categories = categories;
    const QString previous = m_categoryCombo->currentData().toString();
    m_categoryCombo->clear();
    for (const QJsonValue &value : categories) {
        const QJsonObject category = value.toObject();
        const QString id = category.value(QStringLiteral("id")).toString().trimmed();
        if (!id.isEmpty()) {
            m_categoryCombo->addItem(categoryName(category), id);
        }
    }
    if (m_categoryCombo->count() == 0) {
        m_categoryCombo->addItem(QStringLiteral("应用"), QStringLiteral("1"));
    }
    const int index = m_categoryCombo->findData(previous);
    if (index >= 0) {
        m_categoryCombo->setCurrentIndex(index);
    }
}

void MetadataPanel::setGroup(const QJsonObject &group)
{
    m_group = group;
    m_selectedPackagePath = effectivePackagePath(m_group, m_selectedPackagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        m_group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    updateSummary();

    m_appNameEdit->setText(AppJson::stringValue(group, QStringLiteral("app_name_zh"), AppJson::displayName(group)));
    m_websiteEdit->setText(AppJson::stringValue(group, QStringLiteral("website"), group.value(QStringLiteral("homepage")).toString()));
    m_developerNameEdit->setText(AppJson::stringValue(group, QStringLiteral("developer_name"), group.value(QStringLiteral("dev_name")).toString()));
    m_shortDescEdit->setText(AppJson::stringValue(group, QStringLiteral("short_desc_zh"), group.value(QStringLiteral("short_description")).toString()));
    m_fullDescEdit->setPlainText(AppJson::stringValue(group, QStringLiteral("full_desc_zh"), group.value(QStringLiteral("full_description")).toString()));
    m_noteEdit->setPlainText(group.value(QStringLiteral("note_zh")).toString());
    m_appNameEnEdit->setText(group.value(QStringLiteral("app_name_en")).toString());
    m_shortDescEnEdit->setText(group.value(QStringLiteral("short_desc_en")).toString());
    m_fullDescEnEdit->setPlainText(group.value(QStringLiteral("full_desc_en")).toString());
    m_noteEnEdit->setPlainText(group.value(QStringLiteral("note_en")).toString());

    const QString categoryId = group.value(QStringLiteral("category_id")).toString(QStringLiteral("1"));
    const int categoryIndex = m_categoryCombo->findData(categoryId);
    if (categoryIndex >= 0) {
        m_categoryCombo->setCurrentIndex(categoryIndex);
    }

    const QStringList regions = AppJson::stringArray(group.value(QStringLiteral("region_codes")).toArray());
    m_regionChina->setChecked(regions.isEmpty() || regions.contains(QStringLiteral("1")));
    m_regionGlobal->setChecked(regions.contains(QStringLiteral("2")));
    m_replaceAssetsCheck->setChecked(group.value(QStringLiteral("replace_assets")).toBool(false));
    renderPackages();
}

void MetadataPanel::setSelectedPackagePath(const QString &packagePath)
{
    m_selectedPackagePath = effectivePackagePath(m_group, packagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        m_group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    updateSummary();
    renderPackages();
}

QJsonObject MetadataPanel::groupFromUi(const QJsonObject &baseGroup) const
{
    QJsonObject group = baseGroup;
    group.insert(QStringLiteral("app_name_zh"), m_appNameEdit->text().trimmed());
    group.insert(QStringLiteral("website"), m_websiteEdit->text().trimmed());
    group.insert(QStringLiteral("developer_name"), m_developerNameEdit->text().trimmed());
    group.insert(QStringLiteral("short_desc_zh"), m_shortDescEdit->text().trimmed());
    group.insert(QStringLiteral("full_desc_zh"), m_fullDescEdit->toPlainText().trimmed());
    group.insert(QStringLiteral("note_zh"), m_noteEdit->toPlainText().trimmed());
    group.insert(QStringLiteral("app_name_en"), m_appNameEnEdit->text().trimmed());
    group.insert(QStringLiteral("short_desc_en"), m_shortDescEnEdit->text().trimmed());
    group.insert(QStringLiteral("full_desc_en"), m_fullDescEnEdit->toPlainText().trimmed());
    group.insert(QStringLiteral("note_en"), m_noteEnEdit->toPlainText().trimmed());
    const bool manualEnglishEdited = !m_appNameEnEdit->text().trimmed().isEmpty()
        || !m_shortDescEnEdit->text().trimmed().isEmpty()
        || !m_fullDescEnEdit->toPlainText().trimmed().isEmpty()
        || !m_noteEnEdit->toPlainText().trimmed().isEmpty();
    group.insert(QStringLiteral("manual_en_edited"), manualEnglishEdited);
    group.insert(QStringLiteral("replace_assets"), m_replaceAssetsCheck->isChecked());
    const QString mode = baseGroup.value(QStringLiteral("submission_mode")).toString().trimmed();
    group.insert(QStringLiteral("submission_mode"), mode.isEmpty() ? QStringLiteral("auto") : mode);

    QString categoryId = m_categoryCombo->currentData().toString();
    if (categoryId.isEmpty()) {
        categoryId = QStringLiteral("1");
    }
    group.insert(QStringLiteral("category_id"), categoryId);
    group.insert(QStringLiteral("region_codes"), AppJson::regionCodes(m_regionChina->isChecked(), m_regionGlobal->isChecked()));
    if (!m_selectedPackagePath.isEmpty()) {
        group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    return group;
}

void MetadataPanel::renderPackages()
{
    if (m_packageLayout == nullptr) {
        return;
    }
    while (QLayoutItem *item = m_packageLayout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->deleteLater();
        }
        delete item;
    }

    const QJsonArray packages = m_group.value(QStringLiteral("packages")).toArray();
    if (packages.isEmpty()) {
        if (m_packageScroll != nullptr) {
            m_packageScroll->setMinimumHeight(32);
            m_packageScroll->setMaximumHeight(36);
        }
        auto *label = new QLabel(m_group.value(QStringLiteral("online_only")).toBool(false)
                                     ? QStringLiteral("已选择我的应用，拖入新包后生成包列表。")
                                     : QStringLiteral("拖入包后显示包列表。"),
                                 this);
        label->setObjectName(QStringLiteral("MutedText"));
        label->setWordWrap(true);
        label->setMinimumHeight(28);
        m_packageLayout->addWidget(label);
        return;
    }

    if (m_packageScroll != nullptr) {
        const int visibleRows = packages.size() < 3 ? packages.size() : 3;
        const int height = 30 * visibleRows + 4 * (visibleRows - 1) + 2;
        m_packageScroll->setMinimumHeight(height);
        m_packageScroll->setMaximumHeight(packages.size() > 3 ? 102 : height);
    }
    QString selectedPath = effectivePackagePath(m_group, m_selectedPackagePath);
    m_selectedPackagePath = selectedPath;
    for (const QJsonValue &value : packages) {
        const QJsonObject package = value.toObject();
        const QString path = package.value(QStringLiteral("path")).toString().trimmed();
        auto *button = new QPushButton(packageTitle(package), this);
        button->setProperty("class", QStringLiteral("PackageRow"));
        button->setCheckable(true);
        button->setChecked(!path.isEmpty() && path == selectedPath);
        button->setCursor(Qt::PointingHandCursor);
        button->setMinimumHeight(30);
        button->setToolTip(path);
        connect(button, &QPushButton::clicked, this, [this, path]() {
            if (path.isEmpty()) {
                return;
            }
            m_selectedPackagePath = path;
            renderPackages();
            emit packageSelected(path);
        });
        m_packageLayout->addWidget(button);
    }
    m_packageLayout->addStretch(1);
}

void MetadataPanel::updateSummary()
{
    const QJsonObject package = packageForPath(m_group, m_selectedPackagePath);
    const QString packageName = package.value(QStringLiteral("pkg_name")).toString().trimmed();
    const QString packageVersion = package.value(QStringLiteral("version")).toString().trimmed();
    const QString packageArch = package.value(QStringLiteral("arch")).toString().trimmed();
    const QString packageFamily = package.value(QStringLiteral("family")).toString().trimmed();
    const QString packageFormat = package.value(QStringLiteral("format")).toString().trimmed();

    m_pkgNameLabel->setText(packageName.isEmpty() ? AppJson::stringValue(m_group, QStringLiteral("pkg_name"), QStringLiteral("-")) : packageName);
    m_versionLabel->setText(packageVersion.isEmpty() ? AppJson::stringValue(m_group, QStringLiteral("pkg_version"), QStringLiteral("-")) : packageVersion);
    m_archLabel->setText(packageArch.isEmpty() ? AppJson::displayArches(m_group) : packageArch);
    m_familyLabel->setText(QStringLiteral("%1 / %2").arg(
        packageFamily.isEmpty() ? AppJson::stringValue(m_group, QStringLiteral("package_family"), QStringLiteral("-")) : packageFamily,
        packageFormat.isEmpty() ? AppJson::stringValue(m_group, QStringLiteral("package_format"), QStringLiteral("-")) : packageFormat));

    QString iconPath = package.value(QStringLiteral("icon_path")).toString().trimmed();
    if (iconPath.isEmpty()) {
        iconPath = m_group.value(QStringLiteral("icon_path")).toString().trimmed();
    }
    const QPixmap icon(iconPath);
    if (!icon.isNull()) {
        m_iconLabel->setPixmap(icon.scaled(m_iconLabel->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
        m_iconLabel->setText({});
    } else {
        m_iconLabel->setPixmap({});
        m_iconLabel->setText(QStringLiteral("APP"));
    }
}

QLabel *MetadataPanel::makeCaption(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("FieldCaption"));
    label->setMinimumHeight(kControlHeight);
    label->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    return label;
}

QString MetadataPanel::categoryName(const QJsonObject &category)
{
    const QString name = category.value(QStringLiteral("name")).toString().trimmed();
    if (!name.isEmpty()) {
        return name;
    }
    return category.value(QStringLiteral("id")).toString(QStringLiteral("应用"));
}
