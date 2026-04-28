#pragma once

#include <QFrame>
#include <QJsonObject>

class QLabel;
class QLayout;
class QPushButton;
class QVBoxLayout;

class TargetMatrixPanel final : public QFrame
{
    Q_OBJECT
    Q_DISABLE_COPY(TargetMatrixPanel)

public:
    explicit TargetMatrixPanel(QWidget *parent = nullptr);

    void setGroup(const QJsonObject &group);
    void setSelectedPackagePath(const QString &packagePath);
    QJsonObject groupWithTargets(const QJsonObject &baseGroup) const;

private:
    void buildUi();
    void renderSummary();
    void openEditor();
    static void clearLayout(QLayout *layout);

    QJsonObject m_group;
    QString m_selectedPackagePath;
    QVBoxLayout *m_contentLayout = nullptr;
    QLabel *m_emptyLabel = nullptr;
    QPushButton *m_editButton = nullptr;
};
