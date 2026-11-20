#pragma once

#include <QJsonArray>
#include <QPushButton>

class VersionPicker final : public QPushButton
{
    Q_OBJECT

public:
    VersionPicker(QJsonArray options, QJsonArray selected, QString title, QString emptyText, QWidget *parent);

    bool hasOptions() const;
    QJsonArray selected() const;
    void clearSelection();

signals:
    void selectionChanged();

private:
    QString versionName(const QString &id) const;
    void renderSelection();
    void editVersions();

    QJsonArray m_options;
    QJsonArray m_selected;
    QString m_title;
    QString m_emptyText;
};
