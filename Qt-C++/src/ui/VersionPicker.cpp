#include "ui/VersionPicker.h"

#include <QDialog>
#include <QDialogButtonBox>
#include <QJsonObject>
#include <QListWidget>
#include <QStringList>
#include <QVBoxLayout>

#include <utility>

namespace {

QString versionLabel(const QJsonObject &option)
{
    const QString version = option.value(QStringLiteral("version")).toString().trimmed();
    return version.isEmpty() ? option.value(QStringLiteral("id")).toString() : version;
}

} // namespace

VersionPicker::VersionPicker(QJsonArray options, QJsonArray selected, QString title, QString emptyText, QWidget *parent)
    : QPushButton(parent)
    , m_options(std::move(options))
    , m_selected(std::move(selected))
    , m_title(std::move(title))
    , m_emptyText(std::move(emptyText))
{
    for (const QJsonValue &value : m_selected) {
        const QString id = value.toString();
        if (!id.isEmpty() && versionName(id).isEmpty()) {
            m_options.append(QJsonObject{{QStringLiteral("id"), id},
                                         {QStringLiteral("version"), QStringLiteral("版本 %1（历史记录）").arg(id)}});
        }
    }
    setMinimumHeight(30);
    setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);
    setAccessibleName(m_title);
    connect(this, &QPushButton::clicked, this, &VersionPicker::editVersions);
    renderSelection();
}

bool VersionPicker::hasOptions() const
{
    return !m_options.isEmpty();
}

QJsonArray VersionPicker::selected() const
{
    return m_selected;
}

void VersionPicker::clearSelection()
{
    if (!m_selected.isEmpty()) {
        m_selected = {};
        renderSelection();
        emit selectionChanged();
    }
}

QString VersionPicker::versionName(const QString &id) const
{
    for (const QJsonValue &value : m_options) {
        const QJsonObject option = value.toObject();
        if (option.value(QStringLiteral("id")).toString() == id) {
            return versionLabel(option);
        }
    }
    return {};
}

void VersionPicker::renderSelection()
{
    QStringList names;
    for (const QJsonValue &value : m_selected) {
        names.append(versionName(value.toString()));
    }
    if (names.isEmpty()) {
        setText(hasOptions() ? m_emptyText : QStringLiteral("暂无版本"));
    } else {
        setText(names.size() == 1 ? names.first() : QStringLiteral("已选 %1 个版本").arg(names.size()));
    }
    setToolTip(names.isEmpty() ? m_title + QStringLiteral(" · ") + text() : names.join(QStringLiteral("、")));
}

void VersionPicker::editVersions()
{
    QDialog dialog(this);
    dialog.setWindowTitle(m_title);
    dialog.setAttribute(Qt::WA_StyledBackground, true);
    dialog.setStyleSheet(window()->styleSheet());
    dialog.resize(420, 400);
    auto *layout = new QVBoxLayout(&dialog);
    auto *list = new QListWidget(&dialog);
    list->setObjectName(QStringLiteral("SystemVersionList"));
    for (const QJsonValue &value : m_options) {
        const QJsonObject option = value.toObject();
        const QString id = option.value(QStringLiteral("id")).toString();
        if (!id.isEmpty()) {
            auto *item = new QListWidgetItem(versionLabel(option), list);
            item->setData(Qt::UserRole, id);
            item->setCheckState(m_selected.contains(id) ? Qt::Checked : Qt::Unchecked);
        }
    }
    layout->addWidget(list);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    QJsonArray selected;
    for (int index = 0; index < list->count(); ++index) {
        const QListWidgetItem *item = list->item(index);
        if (item->checkState() == Qt::Checked) {
            selected.append(item->data(Qt::UserRole).toString());
        }
    }
    if (selected != m_selected) {
        m_selected = selected;
        renderSelection();
        emit selectionChanged();
    }
}
