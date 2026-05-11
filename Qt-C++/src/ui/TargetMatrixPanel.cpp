#include "ui/TargetMatrixPanel.h"

#include "core/AppJson.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QLayout>
#include <QLayoutItem>
#include <QPushButton>
#include <QScrollArea>
#include <QSizePolicy>
#include <QVBoxLayout>
#include <QVector>

namespace {

QString stringValue(const QJsonObject &object, const QString &key, const QString &fallback = {})
{
    const QString value = object.value(key).toString().trimmed();
    return value.isEmpty() ? fallback : value;
}

QString optionLabel(const QJsonObject &option)
{
    return stringValue(option, QStringLiteral("label"), stringValue(option, QStringLiteral("name"), option.value(QStringLiteral("code")).toString(QStringLiteral("-"))));
}

QJsonArray selectedCodeArray(const QJsonArray &options)
{
    QJsonArray codes;
    for (const QJsonValue &value : options) {
        const QJsonObject option = value.toObject();
        if (!option.value(QStringLiteral("selected")).toBool(false)) {
            continue;
        }
        const QString code = option.value(QStringLiteral("code")).toString().trimmed();
        if (!code.isEmpty()) {
            codes.append(code);
        }
    }
    return codes;
}

QString selectedOptionSummary(const QJsonArray &options)
{
    QStringList labels;
    for (const QJsonValue &value : options) {
        const QJsonObject option = value.toObject();
        if (option.value(QStringLiteral("selected")).toBool(false)) {
            labels.append(optionLabel(option));
        }
    }
    if (labels.isEmpty()) {
        return QStringLiteral("未选择");
    }
    if (labels.size() <= 3) {
        return labels.join(QStringLiteral("、"));
    }
    return QStringLiteral("%1、%2、%3 等 %4 项").arg(labels.at(0), labels.at(1), labels.at(2)).arg(labels.size());
}

QString baselineDisplayName(const QJsonObject &baseline)
{
    return stringValue(baseline, QStringLiteral("version"), baseline.value(QStringLiteral("id")).toString());
}

QString firstString(const QJsonArray &array)
{
    for (const QJsonValue &value : array) {
        const QString text = value.toString().trimmed();
        if (!text.isEmpty()) {
            return text;
        }
    }
    return {};
}

QJsonArray singleStringArray(const QString &value)
{
    QJsonArray result;
    const QString text = value.trimmed();
    if (!text.isEmpty()) {
        result.append(text);
    }
    return result;
}

QString selectedBaselineId(const QJsonObject &target)
{
    const QString selected = firstString(target.value(QStringLiteral("selected_baseline_ids")).toArray());
    return selected.isEmpty() ? target.value(QStringLiteral("baseline_id")).toString().trimmed() : selected;
}

QString unsupportedBaselineId(const QJsonObject &target)
{
    return firstString(target.value(QStringLiteral("unsupported_baseline_ids")).toArray());
}

QString systemLineTitle(const QJsonObject &target)
{
    return stringValue(target, QStringLiteral("label"), target.value(QStringLiteral("code")).toString(QStringLiteral("-")));
}

QString systemSectionName(const QJsonObject &target)
{
    const QString title = systemLineTitle(target);
    if (title.contains(QStringLiteral("专业"))) {
        return QStringLiteral("专业版");
    }
    if (title.contains(QStringLiteral("社区"))) {
        return QStringLiteral("社区版");
    }
    if (title.contains(QStringLiteral("教育"))) {
        return QStringLiteral("教育版");
    }
    return QStringLiteral("其他版本");
}

QString baselineComboText(const QJsonObject &target, const QJsonObject &baseline)
{
    return QStringLiteral("%1 %2").arg(systemLineTitle(target), baselineDisplayName(baseline));
}

void setComboCurrentId(QComboBox *combo, const QString &id)
{
    if (combo == nullptr) {
        return;
    }
    const QString selectedId = id.trimmed();
    for (int index = 0; index < combo->count(); ++index) {
        if (combo->itemData(index).toString() == selectedId) {
            combo->setCurrentIndex(index);
            return;
        }
    }
    combo->setCurrentIndex(0);
}

QLabel *captionLabel(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("FieldCaption"));
    label->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    label->setMinimumWidth(58);
    return label;
}

QLabel *valueLabel(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("SummaryValue"));
    label->setWordWrap(true);
    return label;
}

void addSummaryRow(QVBoxLayout *layout, const QString &caption, const QString &value, QWidget *parent)
{
    auto *row = new QWidget(parent);
    auto *rowLayout = new QHBoxLayout(row);
    rowLayout->setContentsMargins(0, 0, 0, 0);
    rowLayout->setSpacing(8);
    rowLayout->addWidget(captionLabel(caption, row));
    rowLayout->addWidget(valueLabel(value, row), 1);
    layout->addWidget(row);
}

int selectedTargetCount(const QJsonArray &targets)
{
    int count = 0;
    for (const QJsonValue &value : targets) {
        if (value.toObject().value(QStringLiteral("selected")).toBool(false)) {
            ++count;
        }
    }
    return count;
}

int selectedBaselineCount(const QJsonArray &targets)
{
    int count = 0;
    for (const QJsonValue &value : targets) {
        const QJsonObject target = value.toObject();
        if (!target.value(QStringLiteral("selected")).toBool(false)) {
            continue;
        }
        count += target.value(QStringLiteral("selected_baseline_ids")).toArray().size();
    }
    return count;
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
    if (!packagePath.trimmed().isEmpty()) {
        return packagePath;
    }
    return packageForPath(group, {}).value(QStringLiteral("path")).toString();
}

bool packageIsArm(const QJsonObject &package)
{
    const QString arch = package.value(QStringLiteral("arch")).toString().trimmed().toLower();
    return arch == QStringLiteral("arm64") || arch == QStringLiteral("aarch64");
}

bool targetMatchesPackage(const QJsonObject &target, const QString &packagePath)
{
    if (packagePath.trimmed().isEmpty()) {
        return true;
    }
    return target.value(QStringLiteral("package_path")).toString() == packagePath;
}

QJsonArray filteredTargets(const QJsonObject &group, const QString &packagePath)
{
    const QString effectivePath = effectivePackagePath(group, packagePath);
    const QJsonArray targets = group.value(QStringLiteral("targets")).toArray();
    if (effectivePath.isEmpty()) {
        return targets;
    }
    QJsonArray result;
    for (const QJsonValue &value : targets) {
        const QJsonObject target = value.toObject();
        if (targetMatchesPackage(target, effectivePath)) {
            result.append(target);
        }
    }
    return result;
}

QString packageSummaryText(const QJsonObject &group, const QString &packagePath)
{
    const QJsonObject package = packageForPath(group, effectivePackagePath(group, packagePath));
    QString packageName = package.value(QStringLiteral("pkg_name")).toString().trimmed();
    if (packageName.isEmpty()) {
        packageName = group.value(QStringLiteral("pkg_name")).toString().trimmed();
    }
    const QString arch = package.value(QStringLiteral("arch")).toString().trimmed();
    if (packageName.isEmpty()) {
        packageName = AppJson::displayName(group);
    }
    return arch.isEmpty() ? packageName : QStringLiteral("%1  ·  %2").arg(packageName, arch);
}

void inheritDialogStyle(QDialog *dialog, QWidget *parent)
{
    dialog->setAttribute(Qt::WA_StyledBackground, true);
    if (parent != nullptr && parent->window() != nullptr) {
        dialog->setStyleSheet(parent->window()->styleSheet());
    }
}

class TargetConfigDialog final : public QDialog
{
public:
    TargetConfigDialog(const QJsonObject &group, const QString &selectedPackagePath, QWidget *parent)
        : QDialog(parent)
        , m_group(group)
        , m_selectedPackagePath(effectivePackagePath(group, selectedPackagePath))
        , m_showCpu(packageIsArm(packageForPath(group, m_selectedPackagePath)))
    {
        setWindowTitle(QStringLiteral("调整适配范围"));
        inheritDialogStyle(this, parent);
        resize(980, 650);
        buildUi();
    }

    QJsonObject group() const
    {
        return m_group;
    }

private:
    struct TargetBinding {
        int index = -1;
        QString sectionName;
        QCheckBox *checkBox = nullptr;
        QComboBox *baselineCombo = nullptr;
        QComboBox *unsupportedCombo = nullptr;
    };

    struct OptionBinding {
        QString optionKey;
        int index = -1;
        QCheckBox *checkBox = nullptr;
    };

    void buildUi()
    {
        auto *layout = new QVBoxLayout(this);
        layout->setContentsMargins(14, 12, 14, 12);
        layout->setSpacing(8);

        auto *title = new QLabel(QStringLiteral("适配范围"), this);
        title->setObjectName(QStringLiteral("DialogTitle"));
        layout->addWidget(title);

        QString arch = packageForPath(m_group, m_selectedPackagePath).value(QStringLiteral("arch")).toString().trimmed();
        if (arch.isEmpty()) {
            arch = stringValue(m_group, QStringLiteral("adapt_arch_label"), AppJson::displayArches(m_group));
        }
        auto *summary = new QLabel(QStringLiteral("当前包：%1    架构：%2").arg(packageSummaryText(m_group, m_selectedPackagePath), arch.isEmpty() ? QStringLiteral("-") : arch), this);
        summary->setObjectName(QStringLiteral("MutedText"));
        layout->addWidget(summary);

        auto *scroll = new QScrollArea(this);
        scroll->setObjectName(QStringLiteral("FlatScroll"));
        scroll->setWidgetResizable(true);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        auto *container = new QWidget(scroll);
        auto *content = new QVBoxLayout(container);
        content->setContentsMargins(0, 0, 6, 0);
        content->setSpacing(10);

        addTargetRows(content, container);
        addOptionRows(content, container);
        content->addStretch(1);
        scroll->setWidget(container);
        layout->addWidget(scroll, 1);

        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
        layout->addWidget(buttons);
        connect(buttons, &QDialogButtonBox::accepted, this, [this]() {
            collectUi();
            accept();
        });
        connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    }

    void addTargetRows(QVBoxLayout *content, QWidget *parent)
    {
        auto *section = new QLabel(QStringLiteral("系统版本"), parent);
        section->setObjectName(QStringLiteral("CardTitle"));
        content->addWidget(section);

        addTargetGuide(content, parent);

        auto *tools = new QHBoxLayout();
        auto *selectCommonButton = new QPushButton(QStringLiteral("勾选非其他版本"), parent);
        auto *clearButton = new QPushButton(QStringLiteral("清空系统"), parent);
        tools->addWidget(selectCommonButton);
        tools->addWidget(clearButton);
        tools->addStretch(1);
        content->addLayout(tools);

        connect(selectCommonButton, &QPushButton::clicked, this, [this]() {
            for (const TargetBinding &binding : m_targetBindings) {
                if (binding.sectionName != QStringLiteral("其他版本")) {
                    binding.checkBox->setChecked(true);
                }
            }
        });
        connect(clearButton, &QPushButton::clicked, this, [this]() {
            for (const TargetBinding &binding : m_targetBindings) {
                binding.checkBox->setChecked(false);
            }
        });

        const QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        const QJsonArray scopedTargets = filteredTargets(m_group, m_selectedPackagePath);
        if (scopedTargets.isEmpty()) {
            auto *empty = new QLabel(QStringLiteral("当前包类型没有可用系统线。请先同步商店能力缓存后重新解析包。"), parent);
            empty->setObjectName(QStringLiteral("MutedText"));
            empty->setWordWrap(true);
            content->addWidget(empty);
            return;
        }

        QVector<int> professionalRows;
        QVector<int> communityRows;
        QVector<int> educationRows;
        QVector<int> otherRows;
        for (int index = 0; index < targets.size(); ++index) {
            const QJsonObject target = targets.at(index).toObject();
            if (!targetMatchesPackage(target, m_selectedPackagePath)) {
                continue;
            }
            const QString sectionName = systemSectionName(target);
            if (sectionName == QStringLiteral("专业版")) {
                professionalRows.append(index);
            } else if (sectionName == QStringLiteral("社区版")) {
                communityRows.append(index);
            } else if (sectionName == QStringLiteral("教育版")) {
                educationRows.append(index);
            } else {
                otherRows.append(index);
            }
        }

        addTargetSection(content, parent, QStringLiteral("专业版"), professionalRows);
        addTargetSection(content, parent, QStringLiteral("社区版"), communityRows);
        addTargetSection(content, parent, QStringLiteral("教育版"), educationRows);
        addTargetSection(content, parent, QStringLiteral("其他版本"), otherRows);
    }

    void addTargetGuide(QVBoxLayout *content, QWidget *parent)
    {
        auto *guide = new QWidget(parent);
        auto *grid = new QGridLayout(guide);
        grid->setContentsMargins(0, 0, 0, 0);
        grid->setHorizontalSpacing(18);
        grid->setVerticalSpacing(4);

        auto *compat = new QLabel(
            QStringLiteral("<span style='color:#f04444;font-weight:700'>兼容应用基线：</span>"
                           "默认向后兼容。例如：专业版1021，则当前应用默认适配专业版1021及之后版本"),
            guide);
        compat->setTextFormat(Qt::RichText);
        compat->setWordWrap(true);
        compat->setObjectName(QStringLiteral("MutedText"));

        auto *unsupported = new QLabel(
            QStringLiteral("<span style='color:#f04444;font-weight:700'>不上架版本：</span>"
                           "选中后则默认不上架当前版本。例如：专业版1052，则当前应用不上架1052版本"),
            guide);
        unsupported->setTextFormat(Qt::RichText);
        unsupported->setWordWrap(true);
        unsupported->setObjectName(QStringLiteral("MutedText"));

        grid->addWidget(compat, 0, 0);
        grid->addWidget(unsupported, 0, 1);
        grid->setColumnStretch(0, 1);
        grid->setColumnStretch(1, 1);
        content->addWidget(guide);
    }

    void addTargetSection(QVBoxLayout *content, QWidget *parent, const QString &sectionName, const QVector<int> &indexes)
    {
        if (indexes.isEmpty()) {
            return;
        }

        auto *section = new QLabel(sectionName, parent);
        section->setObjectName(QStringLiteral("FieldCaption"));
        content->addWidget(section);

        auto *header = new QWidget(parent);
        auto *headerLayout = new QHBoxLayout(header);
        headerLayout->setContentsMargins(0, 0, 0, 0);
        headerLayout->setSpacing(10);
        auto *lineCaption = new QLabel(QStringLiteral("系统线"), header);
        lineCaption->setObjectName(QStringLiteral("MutedText"));
        auto *baselineCaption = new QLabel(QStringLiteral("兼容应用基线"), header);
        baselineCaption->setObjectName(QStringLiteral("MutedText"));
        auto *unsupportedCaption = new QLabel(QStringLiteral("不上架版本"), header);
        unsupportedCaption->setObjectName(QStringLiteral("MutedText"));
        headerLayout->addWidget(lineCaption);
        headerLayout->addStretch(1);
        headerLayout->addWidget(baselineCaption);
        headerLayout->addSpacing(188);
        headerLayout->addWidget(unsupportedCaption);
        headerLayout->addSpacing(176);
        content->addWidget(header);

        QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        for (int index : indexes) {
            if (index < 0 || index >= targets.size()) {
                continue;
            }
            const QJsonObject target = targets.at(index).toObject();
            auto *row = new QWidget(parent);
            auto *rowLayout = new QHBoxLayout(row);
            rowLayout->setContentsMargins(0, 0, 0, 0);
            rowLayout->setSpacing(10);

            auto *checkBox = new QCheckBox(systemLineTitle(target), row);
            checkBox->setChecked(target.value(QStringLiteral("selected")).toBool(false));
            checkBox->setToolTip(target.value(QStringLiteral("package_path")).toString());
            checkBox->setMinimumWidth(180);
            rowLayout->addWidget(checkBox);

            auto *baselineCombo = createBaselineCombo(
                row,
                target,
                QStringLiteral("未选择具体版本"),
                selectedBaselineId(target));
            rowLayout->addWidget(baselineCombo, 1);

            auto *separator = new QFrame(row);
            separator->setFrameShape(QFrame::VLine);
            separator->setObjectName(QStringLiteral("SoftSeparator"));
            rowLayout->addWidget(separator);

            auto *unsupportedCombo = createBaselineCombo(
                row,
                target,
                QStringLiteral("不设置不上架版本"),
                unsupportedBaselineId(target));
            rowLayout->addWidget(unsupportedCombo, 1);

            content->addWidget(row);
            m_targetBindings.append(TargetBinding{index, sectionName, checkBox, baselineCombo, unsupportedCombo});
        }
    }

    QComboBox *createBaselineCombo(QWidget *parent, const QJsonObject &target, const QString &emptyText, const QString &selectedId)
    {
        auto *combo = new QComboBox(parent);
        combo->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        combo->setMinimumWidth(220);

        const QJsonArray options = target.value(QStringLiteral("baseline_options")).toArray();
        if (options.isEmpty()) {
            combo->addItem(QStringLiteral("未返回具体版本"), QString());
            combo->setEnabled(false);
            return combo;
        }

        combo->addItem(emptyText, QString());
        for (const QJsonValue &value : options) {
            const QJsonObject option = value.toObject();
            const QString id = option.value(QStringLiteral("id")).toString().trimmed();
            if (!id.isEmpty()) {
                combo->addItem(baselineComboText(target, option), id);
            }
        }
        setComboCurrentId(combo, selectedId);
        return combo;
    }

    void addOptionRows(QVBoxLayout *content, QWidget *parent)
    {
        if (m_showCpu) {
            addOptionRow(content, parent, QStringLiteral("CPU"), QStringLiteral("cpu_clip_options"));
        }
        addOptionRow(content, parent, QStringLiteral("主板"), QStringLiteral("motherboard_options"));
    }

    void addOptionRow(QVBoxLayout *content, QWidget *parent, const QString &caption, const QString &optionKey)
    {
        const QJsonArray options = m_group.value(optionKey).toArray();
        if (options.isEmpty()) {
            return;
        }

        auto *section = new QLabel(caption, parent);
        section->setObjectName(QStringLiteral("CardTitle"));
        content->addWidget(section);

        auto *host = new QWidget(parent);
        auto *grid = new QGridLayout(host);
        grid->setContentsMargins(0, 0, 0, 0);
        grid->setHorizontalSpacing(10);
        grid->setVerticalSpacing(5);
        for (int index = 0; index < options.size(); ++index) {
            const QJsonObject option = options.at(index).toObject();
            auto *checkBox = new QCheckBox(optionLabel(option), host);
            checkBox->setChecked(option.value(QStringLiteral("selected")).toBool(false));
            grid->addWidget(checkBox, index / 4, index % 4);
            m_optionBindings.append(OptionBinding{optionKey, index, checkBox});
        }
        grid->setColumnStretch(4, 1);
        content->addWidget(host);
    }

    void collectUi()
    {
        QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        for (const TargetBinding &binding : m_targetBindings) {
            if (binding.index < 0 || binding.index >= targets.size()) {
                continue;
            }
            QJsonObject target = targets.at(binding.index).toObject();
            target.insert(QStringLiteral("selected"), binding.checkBox->isChecked());
            const QString baselineId = binding.baselineCombo == nullptr ? QString() : binding.baselineCombo->currentData().toString().trimmed();
            target.insert(QStringLiteral("selected_baseline_ids"), singleStringArray(baselineId));
            target.insert(QStringLiteral("baseline_id"), baselineId);

            const QString unsupportedId = binding.unsupportedCombo == nullptr ? QString() : binding.unsupportedCombo->currentData().toString().trimmed();
            target.insert(QStringLiteral("unsupported_baseline_ids"), singleStringArray(unsupportedId));
            targets[binding.index] = target;
        }
        m_group.insert(QStringLiteral("targets"), targets);

        for (const OptionBinding &binding : m_optionBindings) {
            QJsonArray options = m_group.value(binding.optionKey).toArray();
            if (binding.index < 0 || binding.index >= options.size()) {
                continue;
            }
            QJsonObject option = options.at(binding.index).toObject();
            option.insert(QStringLiteral("selected"), binding.checkBox->isChecked());
            options[binding.index] = option;
            m_group.insert(binding.optionKey, options);
        }
        if (m_showCpu) {
            m_group.insert(QStringLiteral("cpu_clip_codes"), selectedCodeArray(m_group.value(QStringLiteral("cpu_clip_options")).toArray()));
        }
        m_group.insert(QStringLiteral("motherboard_codes"), selectedCodeArray(m_group.value(QStringLiteral("motherboard_options")).toArray()));
    }

    QJsonObject m_group;
    QString m_selectedPackagePath;
    bool m_showCpu = false;
    QVector<TargetBinding> m_targetBindings;
    QVector<OptionBinding> m_optionBindings;
};

} // namespace

TargetMatrixPanel::TargetMatrixPanel(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("Card"));
    buildUi();
}

void TargetMatrixPanel::buildUi()
{
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(12, 10, 12, 10);
    layout->setSpacing(6);

    auto *titleRow = new QHBoxLayout();
    titleRow->setSpacing(8);
    auto *title = new QLabel(QStringLiteral("适配范围 - 智能匹配"), this);
    title->setObjectName(QStringLiteral("CardTitle"));
    titleRow->addWidget(title, 1);
    m_editButton = new QPushButton(QStringLiteral("调整"), this);
    m_editButton->setCursor(Qt::PointingHandCursor);
    connect(m_editButton, &QPushButton::clicked, this, &TargetMatrixPanel::openEditor);
    titleRow->addWidget(m_editButton);
    layout->addLayout(titleRow);

    m_emptyLabel = new QLabel(QStringLiteral("尚未加载目标矩阵。登录并同步能力缓存后重新解析包。"), this);
    m_emptyLabel->setObjectName(QStringLiteral("MutedText"));
    m_emptyLabel->setWordWrap(true);
    layout->addWidget(m_emptyLabel);

    m_contentLayout = new QVBoxLayout();
    m_contentLayout->setContentsMargins(0, 0, 0, 0);
    m_contentLayout->setSpacing(5);
    layout->addLayout(m_contentLayout);
    layout->addStretch(1);
    renderSummary();
}

void TargetMatrixPanel::setGroup(const QJsonObject &group)
{
    m_group = group;
    m_selectedPackagePath = effectivePackagePath(m_group, m_selectedPackagePath);
    if (!m_selectedPackagePath.isEmpty()) {
        m_group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    renderSummary();
}

void TargetMatrixPanel::setSelectedPackagePath(const QString &packagePath)
{
    const QString effectivePath = effectivePackagePath(m_group, packagePath);
    if (m_selectedPackagePath == effectivePath) {
        return;
    }
    m_selectedPackagePath = effectivePath;
    if (!m_selectedPackagePath.isEmpty()) {
        m_group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    renderSummary();
}

QJsonObject TargetMatrixPanel::groupWithTargets(const QJsonObject &baseGroup) const
{
    QJsonObject group = baseGroup;
    if (m_group.isEmpty()) {
        return group;
    }
    for (const QString &key : {
             QStringLiteral("targets"),
             QStringLiteral("cpu_clip_options"),
             QStringLiteral("cpu_clip_codes"),
             QStringLiteral("motherboard_options"),
             QStringLiteral("motherboard_codes"),
         }) {
        if (m_group.contains(key)) {
            group.insert(key, m_group.value(key));
        }
    }
    if (!m_selectedPackagePath.isEmpty()) {
        group.insert(QStringLiteral("selected_package_path"), m_selectedPackagePath);
    }
    return group;
}

void TargetMatrixPanel::renderSummary()
{
    clearLayout(m_contentLayout);

    const bool hasGroup = !m_group.isEmpty();
    const QJsonArray targets = filteredTargets(m_group, m_selectedPackagePath);
    const bool showCpu = packageIsArm(packageForPath(m_group, effectivePackagePath(m_group, m_selectedPackagePath)));
    m_emptyLabel->setVisible(!hasGroup || targets.isEmpty());
    m_editButton->setEnabled(hasGroup);
    if (!hasGroup) {
        return;
    }

    QString arch = m_group.value(QStringLiteral("adapt_arch_label")).toString().trimmed();
    const QJsonObject selectedPackage = packageForPath(m_group, effectivePackagePath(m_group, m_selectedPackagePath));
    const QString packageArch = selectedPackage.value(QStringLiteral("arch")).toString().trimmed();
    if (!packageArch.isEmpty()) {
        arch = packageArch;
    }
    if (arch.isEmpty()) {
        arch = AppJson::displayArches(m_group);
    }
    addSummaryRow(m_contentLayout, QStringLiteral("当前包"), packageSummaryText(m_group, m_selectedPackagePath), this);
    addSummaryRow(m_contentLayout, QStringLiteral("架构"), arch.isEmpty() ? QStringLiteral("-") : arch, this);
    addSummaryRow(
        m_contentLayout,
        QStringLiteral("系统"),
        QStringLiteral("已选 %1/%2 条系统线").arg(selectedTargetCount(targets)).arg(targets.size()),
        this);
    addSummaryRow(
        m_contentLayout,
        QStringLiteral("版本"),
        QStringLiteral("已选 %1 个具体版本").arg(selectedBaselineCount(targets)),
        this);

    const QJsonArray cpuOptions = m_group.value(QStringLiteral("cpu_clip_options")).toArray();
    if (showCpu && !cpuOptions.isEmpty()) {
        addSummaryRow(m_contentLayout, QStringLiteral("CPU"), selectedOptionSummary(cpuOptions), this);
    }
    const QJsonArray motherboardOptions = m_group.value(QStringLiteral("motherboard_options")).toArray();
    if (!motherboardOptions.isEmpty()) {
        addSummaryRow(m_contentLayout, QStringLiteral("主板"), selectedOptionSummary(motherboardOptions), this);
    }
}

void TargetMatrixPanel::openEditor()
{
    if (m_group.isEmpty()) {
        return;
    }
    TargetConfigDialog dialog(m_group, m_selectedPackagePath, this);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    m_group = dialog.group();
    renderSummary();
}

void TargetMatrixPanel::clearLayout(QLayout *layout)
{
    if (layout == nullptr) {
        return;
    }
    while (QLayoutItem *item = layout->takeAt(0)) {
        if (QWidget *widget = item->widget()) {
            widget->deleteLater();
        }
        if (QLayout *childLayout = item->layout()) {
            clearLayout(childLayout);
            delete childLayout;
        }
        delete item;
    }
}
