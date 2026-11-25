// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMutex>
#include <QString>
#include <future>
#include <memory>

#include "desktopentry.h"

// Maps a window's app_id to its .desktop entry: the display Name and the Icon. It uses the dock's
// entry parser and matcher (desktopentry.h), so the overview names and draws an app exactly as the
// dock does, including taOS app-group apps (X-taOS-AppId, absolute icon paths). The index is built on
// a background thread shortly after start; lookups wait for it only if asked before it is done.
class AppInfo {
public:
    struct Entry { QString name; QString icon; };
    AppInfo();
    static std::shared_ptr<DesktopIndex> scan(const QStringList &dirs);
    static QStringList defaultDirs() { return Desktop::defaultDirs(); }
    void startScan(const QStringList &dirs = defaultDirs());
    void setEntries(const QVector<DesktopEntry> &entries);   // for tests
    Entry lookup(const QString &appId) const;
    // "Terminal" for foot when foot.desktop says Name=Terminal; else the app_id's last dotted segment with its
    // first letter capitalised.
    QString displayName(const QString &appId) const;
private:
    mutable std::shared_future<std::shared_ptr<DesktopIndex>> m_future;
    mutable std::shared_ptr<DesktopIndex> m_index;
    mutable bool m_ready = false;
    void ensure() const;
};
