#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QStringList>

namespace AppJson {

QString stringValue(const QJsonObject &object, const QString &key, const QString &fallback = {});
QStringList stringArray(const QJsonArray &array);
QString joinedStringArray(const QJsonArray &array, const QString &separator);
QString displayName(const QJsonObject &group);
QString displayArches(const QJsonObject &group);
QString firstExistingPath(const QJsonArray &paths);
QString firstScreenshotPath(const QJsonObject &group);
QString safeFileName(QString value, int maxLength = 96);
QJsonArray regionCodes(bool chinaSelected, bool globalSelected);

} // namespace AppJson
