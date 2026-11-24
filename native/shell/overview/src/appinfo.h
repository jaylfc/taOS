// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QMutex>
#include <QString>
#include <future>

// Maps an app_id to its .desktop entry: the display Name and the Icon. The index is built on a
// background thread shortly after start; lookups wait for it only if asked before it is done.
class AppInfo {
public:
    struct Entry { QString name; QString icon; };
    AppInfo();
    // Builds the index from the given application directories (tests pass their own).
    static QHash<QString, Entry> scan(const QStringList &dirs);
    static QStringList defaultDirs();
    void startScan(const QStringList &dirs = defaultDirs());
    void setIndex(QHash<QString, Entry> idx);   // for tests
    Entry lookup(const QString &appId) const;
    // "Terminal" for foot when foot.desktop says Name=Terminal; else the app_id with its first
    // letter capitalised and dots/dashes left alone.
    QString displayName(const QString &appId) const;
private:
    mutable std::shared_future<QHash<QString, Entry>> m_future;
    mutable QHash<QString, Entry> m_cache;
    mutable bool m_ready = false;
    void ensure() const;
};
