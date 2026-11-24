// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QDateTime>
#include <QMutex>
#include <QString>
#include <QStringList>
#include <QVector>
#include <memory>

#include "types.h"

namespace taos::search {

struct FileEntry {
    QString path;
    QString name;    // file name
    QString lname;   // lower case name
    bool dir = false;
};

struct FileLimits { int maxDepth = 6; int maxEntries = 80000; int budgetMs = 2500; };

// A bounded, cancellable name index of the person's home. The walk visits the XDG user folders first,
// skips hidden entries and the usual caches, stops at maxEntries, maxDepth or the deadline, and the
// result is kept in a cache file so the next start has an index before the first walk ends.
class FileIndex {
public:
    using Limits = FileLimits;
    FileIndex(QStringList roots, QString cachePath, Limits lim = {});
    static QStringList defaultRoots();
    static QString defaultCachePath();
    void loadCache();
    // Walk now (call from a worker thread). Returns true when it ran to completion or its bounds.
    bool rebuild(const CancelToken &cancel);
    bool stale(int maxAgeSecs = 600) const;
    std::shared_ptr<const QVector<FileEntry>> snapshot() const;
    qint64 builtAt() const;
private:
    void saveCache(const QVector<FileEntry> &v) const;
    QStringList m_roots;
    QString m_cache;
    Limits m_lim;
    mutable QMutex m_mu;
    std::shared_ptr<const QVector<FileEntry>> m_entries;
    qint64 m_at = 0;
    bool m_building = false;
public:
    bool tryBegin();   // false when a build is already running
    void end();
};

}  // namespace taos::search
