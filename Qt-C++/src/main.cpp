#include "MainWindow.h"

#include <DApplication>
#include <QDir>
#include <QFont>
#include <QFontDatabase>
#include <QStringList>

namespace {

void loadFontsFromDirectory(const QString &directoryPath)
{
    const QDir directory(directoryPath);
    if (!directory.exists()) {
        return;
    }

    const QStringList filters = {
        QStringLiteral("*.ttf"),
        QStringLiteral("*.otf"),
        QStringLiteral("*.ttc"),
    };
    for (const QString &fileName : directory.entryList(filters, QDir::Files)) {
        QFontDatabase::addApplicationFont(directory.absoluteFilePath(fileName));
    }
}

QStringList preferredFontFamilies()
{
    QFontDatabase database;
    const QStringList availableFamilies = database.families();
    const QStringList candidates = {
        QStringLiteral("Noto Sans CJK SC"),
        QStringLiteral("Microsoft YaHei"),
        QStringLiteral("Noto Sans"),
        QStringLiteral("Sans Serif"),
        QStringLiteral("Noto Color Emoji"),
        QStringLiteral("Noto Sans Symbols2"),
        QStringLiteral("Noto Sans Symbols"),
    };

    QStringList families;
    for (const QString &candidate : candidates) {
        if ((candidate == QStringLiteral("Sans Serif") || availableFamilies.contains(candidate)) && !families.contains(candidate)) {
            families.append(candidate);
        }
    }
    return families;
}

void configureApplicationFonts(Dtk::Widget::DApplication &app)
{
    loadFontsFromDirectory(QStringLiteral(APPSTORE_BUNDLED_FONTS_DIR));
    loadFontsFromDirectory(QStringLiteral(APPSTORE_INSTALLED_FONTS_DIR));

    QFont font = app.font();
    const QStringList families = preferredFontFamilies();
    if (!families.isEmpty()) {
        font.setFamilies(families);
    }
    font.setPointSize(10);
    app.setFont(font);
}

} // namespace

int main(int argc, char *argv[])
{
    Dtk::Widget::DApplication app(argc, argv);
    Dtk::Widget::DApplication::setApplicationName(QStringLiteral("UTPublisher"));
    Dtk::Widget::DApplication::setOrganizationName(QStringLiteral("UnionTech"));
    app.setProductName(QStringLiteral("UTPublisher"));
    app.setApplicationDescription(QStringLiteral("应用商店发布工具"));

    configureApplicationFonts(app);

    MainWindow window;
    window.show();
    return app.exec();
}
