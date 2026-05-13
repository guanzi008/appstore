#pragma once

#include <QFrame>
#include <QJsonArray>
#include <QJsonObject>

class QCheckBox;
class QComboBox;
class QLabel;
class QLineEdit;
class QScrollArea;
class QVBoxLayout;
class QTabWidget;
class QTextEdit;

class MetadataPanel final : public QFrame
{
    Q_OBJECT
    Q_DISABLE_COPY(MetadataPanel)

public:
    explicit MetadataPanel(QWidget *parent = nullptr);

    void setCategories(const QJsonArray &categories);
    void setGroup(const QJsonObject &group);
    void setSelectedPackagePath(const QString &packagePath);
    QJsonObject groupFromUi(const QJsonObject &baseGroup) const;

signals:
    void packageSelected(const QString &packagePath);

private:
    void buildUi();
    void renderPackages();
    void updateSummary();
    static QLabel *makeCaption(const QString &text, QWidget *parent);
    static QString categoryName(const QJsonObject &category);

    QJsonArray m_categories;
    QJsonObject m_group;
    QString m_selectedPackagePath;
    QLabel *m_iconLabel = nullptr;
    QLabel *m_pkgNameLabel = nullptr;
    QLabel *m_versionLabel = nullptr;
    QLabel *m_archLabel = nullptr;
    QLabel *m_familyLabel = nullptr;
    QLineEdit *m_appNameEdit = nullptr;
    QLineEdit *m_appNameEnEdit = nullptr;
    QLineEdit *m_websiteEdit = nullptr;
    QLineEdit *m_developerNameEdit = nullptr;
    QLineEdit *m_shortDescEdit = nullptr;
    QLineEdit *m_shortDescEnEdit = nullptr;
    QTextEdit *m_fullDescEdit = nullptr;
    QTextEdit *m_fullDescEnEdit = nullptr;
    QTextEdit *m_noteEdit = nullptr;
    QTextEdit *m_noteEnEdit = nullptr;
    QTabWidget *m_languageTabs = nullptr;
    QComboBox *m_categoryCombo = nullptr;
    QCheckBox *m_regionChina = nullptr;
    QCheckBox *m_regionGlobal = nullptr;
    QCheckBox *m_replaceAssetsCheck = nullptr;
    QScrollArea *m_packageScroll = nullptr;
    QVBoxLayout *m_packageLayout = nullptr;
};
