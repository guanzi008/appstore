#include "MainWindow.h"

#include <DApplication>
#include <QFont>

int main(int argc, char *argv[])
{
    Dtk::Widget::DApplication app(argc, argv);
    Dtk::Widget::DApplication::setApplicationName(QStringLiteral("UTPublisher"));
    Dtk::Widget::DApplication::setOrganizationName(QStringLiteral("UnionTech"));
    app.setProductName(QStringLiteral("UTPublisher"));
    app.setApplicationDescription(QStringLiteral("应用商店发布工具"));

    QFont font = app.font();
    font.setFamilies({QStringLiteral("Noto Sans CJK SC"), QStringLiteral("Microsoft YaHei"), QStringLiteral("Sans Serif")});
    font.setPointSize(10);
    app.setFont(font);

    MainWindow window;
    window.show();
    return app.exec();
}
