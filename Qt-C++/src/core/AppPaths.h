#pragma once

#include <QCoreApplication>
#include <QDir>
#include <QProcessEnvironment>
#include <QStandardPaths>

namespace AppPaths {

inline void setApplicationIdentity()
{
    QCoreApplication::setApplicationName(QStringLiteral("应用投递助手"));
    QCoreApplication::setOrganizationName(QStringLiteral("UnionTech"));
}

inline QProcessEnvironment desktopDataEnvironment()
{
    QProcessEnvironment environment;
    const QString root = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (root.isEmpty()) {
        return environment;
    }
    QDir().mkpath(root);
    const QDir dataDir(root);
    environment.insert(QStringLiteral("UTPUBLISHER_CAPABILITY_CACHE_DIR"), dataDir.filePath(QStringLiteral("capabilities")));
    environment.insert(QStringLiteral("UTPUBLISHER_SESSION_CACHE_DIR"), dataDir.filePath(QStringLiteral("session-state")));
    environment.insert(QStringLiteral("UTPUBLISHER_OUTPUT_ROOT"), dataDir.filePath(QStringLiteral("output")));
    environment.insert(QStringLiteral("UTPUBLISHER_PREFERENCES_PATH"), dataDir.filePath(QStringLiteral("preferences.json")));
    environment.insert(QStringLiteral("UTPUBLISHER_WEBENGINE_CACHE_DIR"), dataDir.filePath(QStringLiteral("webengine")));
    return environment;
}

} // namespace AppPaths
