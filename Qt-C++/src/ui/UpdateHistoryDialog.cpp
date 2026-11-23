#include "ui/UpdateHistoryDialog.h"

#include "core/AppJson.h"

#include <QDialogButtonBox>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QStringList>
#include <QTableWidget>
#include <QVBoxLayout>

namespace {

constexpr int kPageSize = 10;
const QString kHistoryCommand = QStringLiteral("get_app_workflow");
const QString kNotesCommand = QStringLiteral("get_app_history_notes");

} // namespace

UpdateHistoryDialog::UpdateHistoryDialog(
    const QString &appId, const QString &appName, const QString &account, QWidget *parent)
    : QDialog(parent)
    , m_bridge(QStringLiteral(APPSTORE_REPO_ROOT), this)
    , m_appId(appId)
    , m_account(account)
{
    setWindowTitle(QStringLiteral("%1 · 更新记录").arg(appName));
    setMinimumSize(560, 380);
    resize(720, 520);

    auto *layout = new QVBoxLayout(this);
    m_table = new QTableWidget(0, 3, this);
    m_table->setHorizontalHeaderLabels(
        {QStringLiteral("版本"), QStringLiteral("提交时间"), QStringLiteral("状态")});
    m_table->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_table->setSelectionMode(QAbstractItemView::SingleSelection);
    m_table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_table->setAlternatingRowColors(true);
    m_table->verticalHeader()->hide();
    m_table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    layout->addWidget(m_table, 2);

    auto *pagination = new QHBoxLayout();
    m_status = new QLabel(this);
    m_status->setTextFormat(Qt::PlainText);
    m_status->setWordWrap(true);
    pagination->addWidget(m_status, 1);
    m_retryButton = new QPushButton(QStringLiteral("重试"), this);
    m_previousButton = new QPushButton(QStringLiteral("上一页"), this);
    m_nextButton = new QPushButton(QStringLiteral("下一页"), this);
    pagination->addWidget(m_retryButton);
    pagination->addWidget(m_previousButton);
    pagination->addWidget(m_nextButton);
    layout->addLayout(pagination);

    m_notes = new QPlainTextEdit(this);
    m_notes->setReadOnly(true);
    m_notes->setAccessibleName(QStringLiteral("更新说明"));
    m_notes->setPlaceholderText(QStringLiteral("选中一条记录后点击「查看说明」。"));
    layout->addWidget(m_notes, 1);

    auto *buttons = new QDialogButtonBox(this);
    m_notesButton = buttons->addButton(QStringLiteral("查看说明"), QDialogButtonBox::ActionRole);
    buttons->addButton(QStringLiteral("关闭"), QDialogButtonBox::RejectRole);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    layout->addWidget(buttons);

    connect(m_previousButton, &QPushButton::clicked, this, [this]() { loadPage(m_page - 1); });
    connect(m_nextButton, &QPushButton::clicked, this, [this]() { loadPage(m_page + 1); });
    connect(m_retryButton, &QPushButton::clicked, this, [this]() { loadPage(m_requestedPage); });
    connect(m_notesButton, &QPushButton::clicked, this, &UpdateHistoryDialog::loadSelectedNotes);
    connect(m_table, &QTableWidget::cellDoubleClicked, this, &UpdateHistoryDialog::loadSelectedNotes);
    connect(m_table, &QTableWidget::itemSelectionChanged, this, [this]() {
        m_notes->clear();
        updateControls();
    });
    connect(&m_bridge, &BridgeClient::commandFinished, this, [this](const QString &command, const QJsonObject &data) {
        if (command == kHistoryCommand) {
            showPage(data.value(QStringLiteral("history")).toObject());
        } else {
            showNotes(data);
        }
        updateControls();
    });
    connect(&m_bridge, &BridgeClient::commandFailed, this, [this](const QString &command, const QString &message) {
        if (command == kHistoryCommand) {
            m_status->setText(QStringLiteral("更新记录加载失败。%1").arg(message.simplified()));
            m_retryButton->show();
        } else {
            m_notes->setPlainText(QStringLiteral("更新说明加载失败。%1").arg(message.simplified()));
        }
        updateControls();
    });
    loadPage(1);
}

void UpdateHistoryDialog::loadPage(int page)
{
    if (m_bridge.isBusy() || page < 1) {
        return;
    }
    m_requestedPage = page;
    m_retryButton->hide();
    m_status->setText(QStringLiteral("正在加载更新记录…"));
    m_bridge.callAsync(kHistoryCommand, {
        {QStringLiteral("preferred_account"), m_account},
        {QStringLiteral("app_id"), m_appId},
        {QStringLiteral("page_num"), page},
        {QStringLiteral("page_size"), kPageSize},
    }, 150000);
    updateControls();
}

void UpdateHistoryDialog::loadSelectedNotes()
{
    const QString detailId = selectedDetailId();
    if (m_bridge.isBusy() || detailId.isEmpty()) {
        return;
    }
    m_notes->setPlainText(QStringLiteral("正在加载更新说明…"));
    m_bridge.callAsync(kNotesCommand, {
        {QStringLiteral("preferred_account"), m_account},
        {QStringLiteral("detail_id"), detailId},
    }, 150000);
    updateControls();
}

void UpdateHistoryDialog::showPage(const QJsonObject &history)
{
    m_page = history.value(QStringLiteral("page_num")).toInt(m_requestedPage);
    m_total = history.value(QStringLiteral("total")).toInt();
    const QJsonArray rows = history.value(QStringLiteral("rows")).toArray();
    m_table->clearContents();
    m_table->setRowCount(rows.size());
    m_table->setCurrentCell(-1, -1);
    m_notes->clear();
    for (int index = 0; index < rows.size(); ++index) {
        const QJsonObject row = rows.at(index).toObject();
        const QString version = row.value(QStringLiteral("pkg_version")).toString();
        auto *versionItem = new QTableWidgetItem(version.isEmpty() ? QStringLiteral("—") : version);
        versionItem->setData(Qt::UserRole, row.value(QStringLiteral("detail_id")).toString());
        m_table->setItem(index, 0, versionItem);
        m_table->setItem(index, 1, new QTableWidgetItem(row.value(QStringLiteral("submit_time")).toString()));
        m_table->setItem(index, 2, new QTableWidgetItem(row.value(QStringLiteral("status_str")).toString()));
    }
    m_status->setText(m_total == 0 ? QStringLiteral("暂无更新记录")
                                  : QStringLiteral("第 %1/%2 页 · 共 %3 条").arg(m_page).arg(pageCount()).arg(m_total));
}

void UpdateHistoryDialog::showNotes(const QJsonObject &data)
{
    QStringList sections;
    for (const QJsonValue &value : data.value(QStringLiteral("notes")).toArray()) {
        const QJsonObject note = value.toObject();
        const QString language = note.value(QStringLiteral("language")).toString();
        const QString text = note.value(QStringLiteral("text")).toString();
        const QString scope = note.value(QStringLiteral("scope")).toString() == QStringLiteral("package")
            ? QStringLiteral("按包更新说明") : QStringLiteral("通用说明");
        QString heading = language.isEmpty() ? scope : QStringLiteral("%1 · %2").arg(language, scope);
        const QStringList packages = AppJson::stringArray(note.value(QStringLiteral("packages")).toArray());
        if (!packages.isEmpty()) {
            heading += QStringLiteral("\n") + packages.join(QStringLiteral("、"));
        }
        sections.append(QStringLiteral("%1\n%2").arg(heading, text));
    }
    m_notes->setPlainText(sections.isEmpty() ? QStringLiteral("这次提交没有填写更新说明。")
                                            : sections.join(QStringLiteral("\n\n")));
}

void UpdateHistoryDialog::updateControls()
{
    const bool idle = !m_bridge.isBusy();
    m_table->setEnabled(idle);
    m_previousButton->setEnabled(idle && m_page > 1);
    m_nextButton->setEnabled(idle && m_page < pageCount());
    m_retryButton->setEnabled(idle);
    m_notesButton->setEnabled(idle && !selectedDetailId().isEmpty());
}

int UpdateHistoryDialog::pageCount() const
{
    return (m_total + kPageSize - 1) / kPageSize;
}

QString UpdateHistoryDialog::selectedDetailId() const
{
    const QTableWidgetItem *item = m_table->item(m_table->currentRow(), 0);
    return item ? item->data(Qt::UserRole).toString() : QString();
}
