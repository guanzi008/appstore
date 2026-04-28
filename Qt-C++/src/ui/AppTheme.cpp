#include "ui/AppTheme.h"

#include <QColor>
#include <QPalette>
#include <QStringList>
#include <QtGlobal>
#include <QWidget>

namespace {

QString cssColor(const QColor &color)
{
    return color.name(QColor::HexRgb);
}

QString cssRgba(const QColor &color, int alpha)
{
    return QStringLiteral("rgba(%1, %2, %3, %4)")
        .arg(color.red())
        .arg(color.green())
        .arg(color.blue())
        .arg(alpha);
}

QColor mixedColor(QColor base, QColor overlay, double amount)
{
    amount = qBound(0.0, amount, 1.0);
    return QColor(
        static_cast<int>(base.red() * (1.0 - amount) + overlay.red() * amount),
        static_cast<int>(base.green() * (1.0 - amount) + overlay.green() * amount),
        static_cast<int>(base.blue() * (1.0 - amount) + overlay.blue() * amount));
}

} // namespace

namespace AppTheme {

void apply(QWidget *root)
{
    const QPalette palette = root->palette();
    const QColor window = palette.color(QPalette::Window);
    const QColor base = palette.color(QPalette::Base);
    const QColor text = palette.color(QPalette::Text);
    const QColor muted = palette.color(QPalette::Disabled, QPalette::Text);
    const QColor highlight = palette.color(QPalette::Highlight);
    const bool dark = window.lightness() < 128;
    const QColor workspace = dark ? mixedColor(window, QColor(35, 45, 58), 0.55) : mixedColor(window, QColor(235, 244, 252), 0.72);
    const QColor card = dark ? mixedColor(base, QColor(40, 46, 56), 0.45) : mixedColor(base, QColor(255, 255, 255), 0.86);
    const QColor field = dark ? mixedColor(base, QColor(32, 38, 48), 0.7) : mixedColor(base, QColor(241, 247, 252), 0.78);
    const QColor border = dark ? QColor(69, 80, 93) : QColor(196, 210, 224);
    const QColor sidebar = dark ? QColor(25, 32, 41) : QColor(31, 44, 57);
    const QColor sidebarPanel = QColor(255, 255, 255);
    const QColor preview = dark ? QColor(51, 60, 72) : QColor(63, 76, 91);

    QString styleSheet = QStringLiteral(R"(
        QWidget#AppRoot {
            background: %1;
        }
        QFrame#Sidebar {
            background: %2;
            border-right: 1px solid %3;
        }
        QLabel#BrandIcon {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %4, stop:1 %5);
            color: white;
            border-radius: 10px;
            font-weight: 900;
            font-size: 13px;
        }
        QLabel#BrandTitle {
            color: #f8fbff;
            font-size: 18px;
            font-weight: 800;
        }
        QPushButton#SidebarPrimary {
            background: %5;
            color: white;
            border: 0;
            border-radius: 9px;
            padding: 9px 13px;
            text-align: left;
            font-size: 13px;
            font-weight: 800;
        }
        QPushButton[class="AppRow"],
        QPushButton[class="OnlineAppRow"] {
            background: %6;
            color: rgba(255, 255, 255, 205);
            border: 0;
            border-radius: 9px;
            padding: 5px 9px;
            text-align: left;
            font-size: 12px;
            font-weight: 650;
        }
        QPushButton[class="OnlineAppRow"] {
            background: rgba(255, 255, 255, 18);
            color: rgba(255, 255, 255, 214);
            font-size: 11px;
            padding: 4px 8px;
        }
        QPushButton[class="OnlineAppRow"]:checked {
            background: rgba(35, 116, 214, 118);
            color: white;
            border: 1px solid rgba(255, 255, 255, 80);
        }
        QPushButton[class="AppRow"]:checked {
            background: %7;
            color: white;
        }
        QLabel#SidebarSubsection {
            color: rgba(244, 249, 255, 165);
            font-size: 12px;
            font-weight: 760;
            padding: 6px 8px 1px 8px;
        }
        QLabel#SidebarHint {
            color: rgba(244, 249, 255, 140);
            font-size: 12px;
            font-weight: 650;
            padding: 3px 8px;
        }
        QLabel#SidebarSection {
            color: rgba(244, 249, 255, 190);
            font-size: 14px;
            font-weight: 760;
            padding-top: 8px;
        }
        QFrame#TaskCard, QFrame#UserCard {
            background: %8;
            border: 1px solid %9;
            border-radius: 10px;
        }
        QLabel#TaskState {
            color: rgba(235, 244, 255, 178);
            font-size: 12px;
            font-weight: 700;
        }
        QLabel#TaskState[state="running"] {
            color: #ffffff;
        }
        QLabel#TaskState[state="done"] {
            color: #73e28f;
        }
        QLabel#TaskState[state="failed"] {
            color: #ff9b9b;
        }
        QPushButton#SidebarPlain {
            background: transparent;
            color: rgba(246, 250, 255, 190);
            border: 0;
            border-radius: 8px;
            padding: 7px 10px;
            text-align: left;
            font-size: 13px;
            font-weight: 720;
        }
        QPushButton#SidebarPlain:hover {
            background: rgba(255, 255, 255, 24);
        }
        QLabel#Avatar {
            background: %10;
            color: %11;
            border-radius: 20px;
            font-weight: 900;
            font-size: 16px;
        }
        QLabel#UserName {
            color: white;
            font-size: 13px;
            font-weight: 800;
        }
        QLabel#LoginState {
            color: rgba(236, 245, 255, 178);
            font-size: 12px;
            font-weight: 650;
        }
        QPushButton#MiniButton {
            background: rgba(255, 255, 255, 34);
            color: white;
            border: 1px solid rgba(255, 255, 255, 50);
            border-radius: 8px;
            padding: 6px 9px;
            font-weight: 800;
        }
        QFrame#Workspace {
            background: %12;
            border-radius: 0px;
        }
        QLabel#PageTitle, QLabel#DialogTitle {
            color: %13;
            font-size: 16px;
            font-weight: 900;
        }
        QLabel#DialogTitle {
            font-size: 15px;
        }
        QLabel#MutedText {
            color: %14;
            font-size: 12px;
            font-weight: 600;
        }
        QCheckBox {
            color: %13;
            font-size: 12px;
            font-weight: 760;
            spacing: 7px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 5px;
            border: 1px solid %15;
            background: %16;
        }
        QCheckBox::indicator:checked {
            background: %5;
            border: 1px solid %5;
        }
        QFrame#DropZone {
            background: %17;
            border: 2px dashed %18;
            border-radius: 12px;
        }
        QLabel#DropHint {
            color: %13;
            font-size: 13px;
            font-weight: 900;
        }
        QFrame#Card, QFrame#WorkflowBar {
            background: %19;
            border: 1px solid %15;
            border-radius: 10px;
        }
        QLabel#CardTitle {
            color: %13;
            font-size: 14px;
            font-weight: 900;
        }
        QLabel#FieldCaption {
            color: %20;
            font-size: 12px;
            font-weight: 800;
        }
        QLabel#SummaryValue {
            color: %13;
            font-size: 12px;
            font-weight: 900;
        }
        QLabel#PackageIcon {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %4, stop:1 %5);
            color: white;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 900;
        }
        QLabel#PackageIconSmall {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %4, stop:1 %5);
            color: white;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 900;
        }
        QTabWidget#LanguageTabs::pane {
            border: 0;
            background: transparent;
            margin-top: 4px;
        }
        QTabWidget#LanguageTabs QTabBar::tab {
            background: %21;
            color: %20;
            border: 1px solid %15;
            border-radius: 8px;
            padding: 4px 12px;
            margin-right: 5px;
            font-size: 11px;
            font-weight: 800;
        }
        QTabWidget#LanguageTabs QTabBar::tab:selected {
            background: %5;
            color: white;
            border: 1px solid %5;
        }
        QLineEdit, QTextEdit, QComboBox {
            background: %21;
            border: 1px solid %15;
            border-radius: 8px;
            color: %13;
            selection-background-color: %5;
            font-size: 11px;
            font-weight: 620;
        }
        QLineEdit, QComboBox {
            padding: 4px 8px;
        }
        QTextEdit {
            padding: 6px 8px;
        }
        QTextEdit#LargeText {
            font-size: 11px;
        }
        QTextEdit QScrollBar:vertical {
            background: transparent;
            border: 0;
            width: 7px;
            margin: 7px 2px 7px 0px;
        }
        QTextEdit QScrollBar::handle:vertical {
            background: %15;
            border-radius: 3px;
            min-height: 24px;
        }
        QTextEdit QScrollBar::add-line:vertical,
        QTextEdit QScrollBar::sub-line:vertical,
        QTextEdit QScrollBar::add-page:vertical,
        QTextEdit QScrollBar::sub-page:vertical {
            background: transparent;
            border: 0;
            height: 0px;
        }
        QDialog {
            background: %19;
            color: %13;
        }
        QDialog QLabel {
            color: %13;
        }
        QDialog QLabel#DialogTitle,
        QDialog QLabel#CardTitle {
            color: %13;
            font-size: 15px;
            font-weight: 900;
        }
        QDialog QLabel#MutedText,
        QDialog QLabel#FieldCaption,
        QDialog QLabel#SummaryValue {
            color: %20;
            font-size: 12px;
            font-weight: 720;
        }
        QDialog QCheckBox {
            color: %13;
            font-size: 12px;
            font-weight: 760;
            spacing: 10px;
        }
        QDialog QScrollArea#FlatScroll {
            background: %19;
            border: 0;
        }
        QDialog QScrollArea#FlatScroll QWidget {
            background: %19;
        }
        QDialog QScrollArea#FlatScroll > QWidget > QWidget {
            background: %19;
        }
        QDialog QDialogButtonBox {
            background: %19;
        }
        QFrame#Workspace QPushButton,
        QFrame#Card QPushButton,
        QFrame#WorkflowBar QPushButton,
        QFrame#DropZone QPushButton,
        QDialog QPushButton {
            background: %22;
            color: %13;
            border: 1px solid %15;
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 11px;
            font-weight: 800;
        }
        QFrame#Workspace QPushButton:hover,
        QFrame#Card QPushButton:hover,
        QFrame#WorkflowBar QPushButton:hover,
        QFrame#DropZone QPushButton:hover,
        QDialog QPushButton:hover {
            background: %23;
        }
        QFrame#Card QPushButton[class="PackageRow"] {
            background: %21;
            color: %13;
            border: 1px solid %15;
            border-radius: 8px;
            padding: 5px 8px;
            text-align: left;
            font-size: 11px;
            font-weight: 760;
        }
        QFrame#Card QPushButton[class="PackageRow"]:checked {
            background: %17;
            color: %13;
            border: 1px solid %18;
        }
        QLabel#ScreenshotPreview {
            background: %24;
            color: rgba(255, 255, 255, 215);
            border-radius: 11px;
            font-size: 13px;
            font-weight: 900;
        }
        QFrame#Card QPushButton[class="ScreenshotThumb"] {
            background: %21;
            color: %20;
            border: 1px solid %15;
            border-radius: 8px;
            padding: 5px 8px;
            font-size: 11px;
            font-weight: 750;
        }
        QFrame#Card QPushButton[class="ScreenshotThumb"]:checked {
            background: %17;
            color: %13;
            border: 1px solid %18;
        }
        QScrollArea#FlatScroll {
            border: 0;
            background: transparent;
        }
        QScrollArea#FlatScroll > QWidget > QWidget {
            background: transparent;
        }
        QLabel#WorkflowStep {
            background: %21;
            color: %20;
            border: 1px solid %15;
            border-radius: 9px;
            padding: 0px 8px;
            font-size: 12px;
            font-weight: 850;
        }
        QLabel#WorkflowStep[state="running"] {
            background: %17;
            color: %13;
            border: 1px solid %18;
        }
        QLabel#WorkflowStep[state="done"] {
            background: rgba(57, 181, 94, 38);
            color: #24964c;
            border: 1px solid rgba(57, 181, 94, 120);
        }
        QLabel#WorkflowStep[state="failed"] {
            background: rgba(224, 70, 70, 38);
            color: #d23b3b;
            border: 1px solid rgba(224, 70, 70, 115);
        }
        QLabel#WorkflowText {
            color: %13;
            font-size: 13px;
            font-weight: 900;
        }
        QFrame#WorkflowBar QPushButton#PrimaryAction {
            background: %5;
            color: white;
            border: 1px solid %5;
            border-radius: 9px;
            padding: 0px 18px;
            font-size: 12px;
            font-weight: 900;
        }
    )");
    const QStringList values = {
        cssColor(workspace),
        cssColor(sidebar),
        cssRgba(QColor(255, 255, 255), 28),
        cssColor(highlight.lighter(135)),
        cssColor(highlight),
        cssRgba(sidebarPanel, 22),
        cssRgba(sidebarPanel, 45),
        cssRgba(sidebarPanel, 28),
        cssRgba(sidebarPanel, 35),
        dark ? QStringLiteral("#303947") : QStringLiteral("#f4f7fb"),
        dark ? QStringLiteral("#e8eef6") : QStringLiteral("#5f6b78"),
        cssColor(workspace),
        cssColor(text),
        cssColor(muted),
        cssColor(border),
        cssColor(field),
        cssRgba(highlight.lighter(170), dark ? 38 : 58),
        cssColor(highlight),
        cssColor(card),
        cssColor(dark ? text.darker(110) : QColor(64, 80, 100)),
        cssColor(field),
        cssColor(mixedColor(field, card, 0.45)),
        cssColor(base),
        cssColor(preview),
    };
    for (int index = values.size(); index >= 1; --index) {
        styleSheet.replace(QStringLiteral("%") + QString::number(index), values.at(index - 1));
    }
    root->setStyleSheet(styleSheet);
}

} // namespace AppTheme
