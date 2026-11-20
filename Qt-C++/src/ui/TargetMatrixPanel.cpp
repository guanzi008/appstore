#include "ui/TargetMatrixPanel.h"

#include "core/AppJson.h"
#include "ui/VersionPicker.h"

#include <QCheckBox>
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

QJsonArray selectedBaselines(const QJsonObject &target)
{
    const QJsonArray selected = target.value(QStringLiteral("selected_baseline_ids")).toArray();
    return selected.isEmpty() ? singleStringArray(target.value(QStringLiteral("baseline_id")).toString()) : selected;
}

QString systemLineTitle(const QJsonObject &target)
{
    const QString title = stringValue(target, QStringLiteral("label"), target.value(QStringLiteral("code")).toString(QStringLiteral("-")));
    const QStringList prefixes{QStringLiteral("professional"), QStringLiteral("community"), QStringLiteral("education")};
    const QStringList names{QStringLiteral("专业版"), QStringLiteral("社区版"), QStringLiteral("教育版")};
    for (int index = 0; index < prefixes.size(); ++index) {
        if (title.startsWith(prefixes.at(index), Qt::CaseInsensitive)) {
            return names.at(index) + title.mid(prefixes.at(index).size());
        }
    }
    return title;
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
        count += selectedBaselines(target).size();
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
        resize(760, 570);
        buildUi();
    }

    QJsonObject group() const
    {
        return m_group;
    }

private:
    struct TargetBinding {
        int index = -1;
        QCheckBox *checkBox = nullptr;
        VersionPicker *baselinePicker = nullptr;
        VersionPicker *unsupportedPicker = nullptr;
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

        auto *summary = new QLabel(packageSummaryText(m_group, m_selectedPackagePath), this);
        summary->setObjectName(QStringLiteral("MutedText"));
        summary->setTextFormat(Qt::PlainText);
        summary->setWordWrap(true);
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
        auto *section = new QLabel(QStringLiteral("系统线"), parent);
        section->setObjectName(QStringLiteral("CardTitle"));
        content->addWidget(section);

        const QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        const QJsonArray scopedTargets = filteredTargets(m_group, m_selectedPackagePath);
        if (scopedTargets.isEmpty()) {
            auto *empty = new QLabel(QStringLiteral("当前包类型没有可用系统线。请先同步商店能力缓存后重新解析包。"), parent);
            empty->setObjectName(QStringLiteral("MutedText"));
            empty->setWordWrap(true);
            content->addWidget(empty);
            return;
        }

        auto *guide = new QLabel(QStringLiteral("先勾选系统线。兼容基线留空时使用商店默认范围，排除的版本不会上架。"), parent);
        guide->setObjectName(QStringLiteral("MutedText"));
        guide->setWordWrap(true);
        content->addWidget(guide);
        auto *clearButton = new QPushButton(QStringLiteral("清空系统线"), parent);
        content->addWidget(clearButton, 0, Qt::AlignLeft);
        connect(clearButton, &QPushButton::clicked, this, [this]() {
            for (const TargetBinding &binding : m_targetBindings) {
                binding.checkBox->setChecked(false);
            }
        });

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

    void addTargetSection(QVBoxLayout *content, QWidget *parent, const QString &sectionName, const QVector<int> &indexes)
    {
        if (indexes.isEmpty()) {
            return;
        }

        auto *grid = new QGridLayout();
        grid->setHorizontalSpacing(12);
        grid->setVerticalSpacing(5);
        grid->setColumnStretch(0, 2);
        grid->setColumnStretch(1, 2);
        grid->setColumnStretch(2, 2);
        grid->addWidget(captionLabel(sectionName, parent), 0, 0);
        grid->addWidget(captionLabel(QStringLiteral("兼容基线"), parent), 0, 1);
        grid->addWidget(captionLabel(QStringLiteral("不上架版本"), parent), 0, 2);

        const QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        int row = 1;
        for (int index : indexes) {
            const QJsonObject target = targets.at(index).toObject();
            auto *checkBox = new QCheckBox(systemLineTitle(target), parent);
            checkBox->setChecked(target.value(QStringLiteral("selected")).toBool(false));
            checkBox->setToolTip(QStringLiteral("系统线 %1").arg(target.value(QStringLiteral("code")).toString()));
            const QJsonArray options = target.value(QStringLiteral("baseline_options")).toArray();
            const QString title = systemLineTitle(target) + QStringLiteral(" · ");
            auto *baseline = new VersionPicker(options, selectedBaselines(target), title + QStringLiteral("兼容基线"),
                                               QStringLiteral("商店默认"), parent);
            auto *unsupported = new VersionPicker(options, target.value(QStringLiteral("unsupported_baseline_ids")).toArray(),
                                                  title + QStringLiteral("不上架版本"), QStringLiteral("无排除版本"), parent);
            auto enablePickers = [baseline, unsupported](bool checked) {
                baseline->setEnabled(checked && baseline->hasOptions());
                unsupported->setEnabled(checked && unsupported->hasOptions());
            };
            enablePickers(checkBox->isChecked());
            connect(checkBox, &QCheckBox::toggled, this, enablePickers);
            grid->addWidget(checkBox, row, 0);
            grid->addWidget(baseline, row, 1);
            grid->addWidget(unsupported, row++, 2);
            m_targetBindings.append(TargetBinding{index, checkBox, baseline, unsupported});
        }
        content->addLayout(grid);
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
            const QJsonArray baselineIds = binding.baselinePicker->selected();
            target.insert(QStringLiteral("selected_baseline_ids"), baselineIds);
            target.insert(QStringLiteral("baseline_id"), firstString(baselineIds));
            target.insert(QStringLiteral("unsupported_baseline_ids"), binding.unsupportedPicker->selected());
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

bool TargetMatrixPanel::editTargets(QJsonObject *group, const QString &packagePath, QWidget *parent)
{
    if (group == nullptr || group->isEmpty()) {
        return false;
    }
    TargetConfigDialog dialog(*group, packagePath, parent);
    if (dialog.exec() != QDialog::Accepted) {
        return false;
    }
    *group = dialog.group();
    const QString selectedPath = effectivePackagePath(*group, packagePath);
    if (!selectedPath.isEmpty()) {
        group->insert(QStringLiteral("selected_package_path"), selectedPath);
    }
    return true;
}

QStringList TargetMatrixPanel::selectedSystemLines(const QJsonObject &group, const QString &packagePath)
{
    QStringList labels;
    for (const QJsonValue &value : filteredTargets(group, packagePath)) {
        const QJsonObject target = value.toObject();
        if (!target.value(QStringLiteral("selected")).toBool(false)) {
            continue;
        }
        const int baselines = selectedBaselines(target).size();
        const int exclusions = target.value(QStringLiteral("unsupported_baseline_ids")).toArray().size();
        QString label = systemLineTitle(target);
        if (baselines > 0) {
            label += QStringLiteral(" · %1 个基线").arg(baselines);
        }
        if (exclusions > 0) {
            label += QStringLiteral(" · 排除 %1 个版本").arg(exclusions);
        }
        labels.append(label);
    }
    return labels;
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
