#include "core/AppJson.h"

#include <QFileInfo>

namespace AppJson {

QString stringValue(const QJsonObject &object, const QString &key, const QString &fallback)
{
    const QString value = object.value(key).toString().trimmed();
    return value.isEmpty() ? fallback : value;
}

QStringList stringArray(const QJsonArray &array)
{
    QStringList values;
    for (const QJsonValue &value : array) {
        const QString text = value.toString().trimmed();
        if (!text.isEmpty()) {
            values.append(text);
        }
    }
    return values;
}

QString joinedStringArray(const QJsonArray &array, const QString &separator)
{
    return stringArray(array).join(separator);
}

QString displayName(const QJsonObject &group)
{
    return stringValue(group, QStringLiteral("display_name"), stringValue(group, QStringLiteral("pkg_name"), QStringLiteral("-")));
}

QString displayArches(const QJsonObject &group)
{
    const QString arches = joinedStringArray(group.value(QStringLiteral("pkg_arches")).toArray(), QStringLiteral(", "));
    return arches.isEmpty() ? QStringLiteral("-") : arches;
}

QString firstExistingPath(const QJsonArray &paths)
{
    for (const QJsonValue &value : paths) {
        const QString path = value.toString().trimmed();
        if (!path.isEmpty() && QFileInfo::exists(path)) {
            return path;
        }
    }
    return {};
}

QString firstScreenshotPath(const QJsonObject &group)
{
    return firstExistingPath(group.value(QStringLiteral("screenshot_paths")).toArray());
}

QString safeFileName(QString value, int maxLength)
{
    if (value.trimmed().isEmpty()) {
        value = QStringLiteral("app");
    }

    QString safe;
    for (const QChar ch : value) {
        if (ch.isLetterOrNumber() || ch == QLatin1Char('.') || ch == QLatin1Char('-') || ch == QLatin1Char('_')) {
            safe.append(ch);
        } else {
            safe.append(QLatin1Char('_'));
        }
    }
    return safe.left(maxLength);
}

QJsonArray regionCodes(bool chinaSelected, bool globalSelected)
{
    QJsonArray regions;
    if (chinaSelected) {
        regions.append(QStringLiteral("1"));
    }
    if (globalSelected) {
        regions.append(QStringLiteral("2"));
    }
    if (regions.isEmpty()) {
        regions.append(QStringLiteral("1"));
    }
    return regions;
}

} // namespace AppJson
