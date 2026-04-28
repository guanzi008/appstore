#include "ui/TargetMatrixPanel.h"

#include "core/AppJson.h"

#include <QCheckBox>
#include <QDialog>
#include <QDialogButtonBox>
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

namespace {

QString stringValue(const QJsonObject &object, const QString &key, const QString &fallback = {})
{
    const QString value = object.value(key).toString().trimmed();
    return value.isEmpty() ? fallback : value;
}

QStringList stringArray(const QJsonArray &array)
{
    QStringList result;
    for (const QJsonValue &value : array) {
        const QString text = value.toString().trimmed();
        if (!text.isEmpty() && !result.contains(text)) {
            result.append(text);
        }
    }
    return result;
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

QString baselineDisplayNameById(const QJsonArray &options, const QString &baselineId)
{
    for (const QJsonValue &value : options) {
        const QJsonObject option = value.toObject();
        if (option.value(QStringLiteral("id")).toString() == baselineId) {
            return baselineDisplayName(option);
        }
    }
    return baselineId;
}

QString baselineSummary(const QJsonObject &target)
{
    const QJsonArray options = target.value(QStringLiteral("baseline_options")).toArray();
    if (options.isEmpty()) {
        return QStringLiteral("未返回具体版本");
    }
    const QStringList selectedIds = stringArray(target.value(QStringLiteral("selected_baseline_ids")).toArray());
    if (selectedIds.isEmpty()) {
        return QStringLiteral("未选择具体版本");
    }
    QStringList labels;
    for (const QString &id : selectedIds) {
        labels.append(baselineDisplayNameById(options, id));
    }
    if (labels.size() == 1) {
        return labels.first();
    }
    if (labels.size() <= 3) {
        return QStringLiteral("已选 %1 项：%2").arg(labels.size()).arg(labels.join(QStringLiteral("、")));
    }
    return QStringLiteral("已选 %1 项：%2、%3、%4...").arg(labels.size()).arg(labels.at(0), labels.at(1), labels.at(2));
}

QString baselineTooltip(const QJsonObject &target)
{
    const QStringList selectedIds = stringArray(target.value(QStringLiteral("selected_baseline_ids")).toArray());
    if (selectedIds.isEmpty()) {
        return QStringLiteral("当前系统线未选择具体版本。");
    }
    QStringList labels;
    const QJsonArray options = target.value(QStringLiteral("baseline_options")).toArray();
    for (const QString &id : selectedIds) {
        labels.append(QStringLiteral("%1 (%2)").arg(baselineDisplayNameById(options, id), id));
    }
    return labels.join(QLatin1Char('\n'));
}

QJsonArray allBaselineIds(const QJsonArray &options)
{
    QJsonArray ids;
    for (const QJsonValue &value : options) {
        const QString id = value.toObject().value(QStringLiteral("id")).toString().trimmed();
        if (!id.isEmpty()) {
            ids.append(id);
        }
    }
    return ids;
}

QJsonArray latestBaselineId(const QJsonArray &options)
{
    QJsonArray ids;
    if (!options.isEmpty()) {
        const QString id = options.last().toObject().value(QStringLiteral("id")).toString().trimmed();
        if (!id.isEmpty()) {
            ids.append(id);
        }
    }
    return ids;
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

QString targetTitle(const QJsonObject &target)
{
    const QString label = stringValue(target, QStringLiteral("label"), target.value(QStringLiteral("code")).toString());
    const QString arch = target.value(QStringLiteral("package_arch")).toString().trimmed();
    return arch.isEmpty() ? label : QStringLiteral("%1  %2").arg(label, arch);
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

class BaselineSelectionDialog final : public QDialog
{
public:
    BaselineSelectionDialog(const QJsonArray &options, const QJsonArray &selectedIds, QWidget *parent)
        : QDialog(parent)
        , m_options(options)
    {
        setWindowTitle(QStringLiteral("选择具体系统版本"));
        inheritDialogStyle(this, parent);
        resize(420, 480);

        auto *layout = new QVBoxLayout(this);
        layout->setContentsMargins(14, 12, 14, 12);
        layout->setSpacing(8);

        auto *title = new QLabel(QStringLiteral("具体系统版本"), this);
        title->setObjectName(QStringLiteral("DialogTitle"));
        layout->addWidget(title);

        auto *toolbar = new QHBoxLayout();
        auto *latestButton = new QPushButton(QStringLiteral("只选最新"), this);
        auto *allButton = new QPushButton(QStringLiteral("全选"), this);
        auto *clearButton = new QPushButton(QStringLiteral("清空"), this);
        toolbar->addWidget(latestButton);
        toolbar->addWidget(allButton);
        toolbar->addWidget(clearButton);
        toolbar->addStretch(1);
        layout->addLayout(toolbar);

        auto *scroll = new QScrollArea(this);
        scroll->setObjectName(QStringLiteral("FlatScroll"));
        scroll->setWidgetResizable(true);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        auto *container = new QWidget(scroll);
        auto *optionsLayout = new QVBoxLayout(container);
        optionsLayout->setContentsMargins(0, 0, 4, 0);
        optionsLayout->setSpacing(4);

        const QStringList selected = stringArray(selectedIds);
        for (const QJsonValue &value : options) {
            const QJsonObject option = value.toObject();
            const QString id = option.value(QStringLiteral("id")).toString().trimmed();
            auto *checkBox = new QCheckBox(QStringLiteral("%1  (%2)").arg(baselineDisplayName(option), id), container);
            checkBox->setChecked(selected.contains(id));
            optionsLayout->addWidget(checkBox);
            m_checkBoxes.append(checkBox);
        }
        optionsLayout->addStretch(1);
        scroll->setWidget(container);
        layout->addWidget(scroll, 1);

        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
        layout->addWidget(buttons);

        connect(latestButton, &QPushButton::clicked, this, [this]() {
            const QJsonArray latest = latestBaselineId(m_options);
            const QStringList ids = stringArray(latest);
            for (int index = 0; index < m_checkBoxes.size(); ++index) {
                const QString id = m_options.at(index).toObject().value(QStringLiteral("id")).toString().trimmed();
                m_checkBoxes.at(index)->setChecked(ids.contains(id));
            }
        });
        connect(allButton, &QPushButton::clicked, this, [this]() {
            for (QCheckBox *checkBox : m_checkBoxes) {
                checkBox->setChecked(true);
            }
        });
        connect(clearButton, &QPushButton::clicked, this, [this]() {
            for (QCheckBox *checkBox : m_checkBoxes) {
                checkBox->setChecked(false);
            }
        });
        connect(buttons, &QDialogButtonBox::accepted, this, &QDialog::accept);
        connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    }

    QJsonArray selectedIds() const
    {
        QJsonArray result;
        for (int index = 0; index < m_checkBoxes.size(); ++index) {
            if (!m_checkBoxes.at(index)->isChecked()) {
                continue;
            }
            const QString id = m_options.at(index).toObject().value(QStringLiteral("id")).toString().trimmed();
            if (!id.isEmpty()) {
                result.append(id);
            }
        }
        return result;
    }

private:
    QJsonArray m_options;
    QVector<QCheckBox *> m_checkBoxes;
};

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
        resize(720, 620);
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
        QPushButton *baselineButton = nullptr;
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

        auto *tools = new QHBoxLayout();
        auto *selectAllButton = new QPushButton(QStringLiteral("全选系统"), parent);
        auto *clearButton = new QPushButton(QStringLiteral("清空系统"), parent);
        tools->addWidget(selectAllButton);
        tools->addWidget(clearButton);
        tools->addStretch(1);
        content->addLayout(tools);

        connect(selectAllButton, &QPushButton::clicked, this, [this]() {
            for (const TargetBinding &binding : m_targetBindings) {
                binding.checkBox->setChecked(true);
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

        for (int index = 0; index < targets.size(); ++index) {
            const QJsonObject target = targets.at(index).toObject();
            if (!targetMatchesPackage(target, m_selectedPackagePath)) {
                continue;
            }
            auto *row = new QWidget(parent);
            auto *rowLayout = new QHBoxLayout(row);
            rowLayout->setContentsMargins(0, 0, 0, 0);
            rowLayout->setSpacing(8);

            auto *checkBox = new QCheckBox(targetTitle(target), row);
            checkBox->setChecked(target.value(QStringLiteral("selected")).toBool(false));
            checkBox->setToolTip(target.value(QStringLiteral("package_path")).toString());
            rowLayout->addWidget(checkBox, 1);

            auto *baselineButton = new QPushButton(baselineSummary(target), row);
            baselineButton->setToolTip(baselineTooltip(target));
            baselineButton->setEnabled(!target.value(QStringLiteral("baseline_options")).toArray().isEmpty());
            baselineButton->setMinimumWidth(180);
            rowLayout->addWidget(baselineButton);
            content->addWidget(row);

            m_targetBindings.append(TargetBinding{index, checkBox, baselineButton});
            connect(baselineButton, &QPushButton::clicked, this, [this, index, baselineButton]() {
                openBaselineSelector(index, baselineButton);
            });
        }
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

    void openBaselineSelector(int targetIndex, QPushButton *button)
    {
        QJsonArray targets = m_group.value(QStringLiteral("targets")).toArray();
        if (targetIndex < 0 || targetIndex >= targets.size()) {
            return;
        }
        QJsonObject target = targets.at(targetIndex).toObject();
        const QJsonArray options = target.value(QStringLiteral("baseline_options")).toArray();
        if (options.isEmpty()) {
            return;
        }

        BaselineSelectionDialog dialog(options, target.value(QStringLiteral("selected_baseline_ids")).toArray(), this);
        if (dialog.exec() != QDialog::Accepted) {
            return;
        }
        const QJsonArray selectedIds = dialog.selectedIds();
        target.insert(QStringLiteral("selected_baseline_ids"), selectedIds);
        target.insert(QStringLiteral("baseline_id"), selectedIds.isEmpty() ? QString() : selectedIds.first().toString());
        targets[targetIndex] = target;
        m_group.insert(QStringLiteral("targets"), targets);
        button->setText(baselineSummary(target));
        button->setToolTip(baselineTooltip(target));
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
            if (target.value(QStringLiteral("selected_baseline_ids")).toArray().isEmpty()) {
                const QJsonArray latest = latestBaselineId(target.value(QStringLiteral("baseline_options")).toArray());
                target.insert(QStringLiteral("selected_baseline_ids"), latest);
                target.insert(QStringLiteral("baseline_id"), latest.isEmpty() ? QString() : latest.first().toString());
            }
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
    auto *title = new QLabel(QStringLiteral("🔗 适配范围 - 智能匹配"), this);
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
