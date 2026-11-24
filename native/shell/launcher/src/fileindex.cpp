// SPDX-License-Identifier: AGPL-3.0-or-later
#include "fileindex.h"

#include <QDateTime>
#include <QDir>
#include <QDirIterator>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QSaveFile>
#include <QSet>
#include <QStandardPaths>

namespace taos::search {

QStringList FileIndex::defaultRoots() {
    QStringList r;
    for (auto t : { QStandardPaths::DesktopLocation, QStandardPaths::DocumentsLocation, QStandardPaths::DownloadLocation,
                    QStandardPaths::PicturesLocation, QStandardPaths::MusicLocation, QStandardPaths::MoviesLocation })
        for (const QString &p : QStandardPaths::standardLocations(t)) if (!r.contains(p) && QFileInfo(p).isDir()) r << p;
    const QString home = QDir::homePath();
    if (!r.contains(home)) r << home;
    return r;
}

QString FileIndex::defaultCachePath() {
    QString base = qEnvironmentVariable("XDG_CACHE_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.cache");
    return base + QStringLiteral("/taos/launcher-files.txt");
}

FileIndex::FileIndex(QStringList roots, QString cachePath, Limits lim) : m_roots(std::move(roots)), m_cache(std::move(cachePath)), m_lim(lim) {
    m_entries = std::make_shared<QVector<FileEntry>>();
}

bool FileIndex::tryBegin() { QMutexLocker l(&m_mu); if (m_building) return false; m_building = true; return true; }
void FileIndex::end() { QMutexLocker l(&m_mu); m_building = false; }

std::shared_ptr<const QVector<FileEntry>> FileIndex::snapshot() const { QMutexLocker l(&m_mu); return m_entries; }
qint64 FileIndex::builtAt() const { QMutexLocker l(&m_mu); return m_at; }
bool FileIndex::stale(int maxAge) const { QMutexLocker l(&m_mu); return QDateTime::currentSecsSinceEpoch() - m_at > maxAge; }

// cache format: first line "taos-files 1 <epoch>", then one "d|f<TAB>path" per line
void FileIndex::loadCache() {
    QFile f(m_cache);
    if (!f.open(QIODevice::ReadOnly)) return;
    const QList<QByteArray> lines = f.readAll().split('\n');
    if (lines.isEmpty()) return;
    const QList<QByteArray> head = lines[0].split(' ');
    if (head.size() != 3 || head[0] != "taos-files" || head[1] != "1") return;
    auto v = std::make_shared<QVector<FileEntry>>();
    v->reserve(lines.size());
    for (int i = 1; i < lines.size(); ++i) {
        const QByteArray &l = lines[i];
        if (l.size() < 3 || l[1] != '\t') continue;
        FileEntry e;
        e.dir = l[0] == 'd';
        e.path = QString::fromUtf8(l.mid(2));
        e.name = QFileInfo(e.path).fileName();
        e.lname = e.name.toLower();
        v->append(e);
    }
    QMutexLocker l(&m_mu);
    m_entries = v;
    m_at = head[2].toLongLong();
}

void FileIndex::saveCache(const QVector<FileEntry> &v) const {
    QDir().mkpath(QFileInfo(m_cache).absolutePath());
    QSaveFile f(m_cache);
    if (!f.open(QIODevice::WriteOnly)) return;
    f.write(QStringLiteral("taos-files 1 %1\n").arg(QDateTime::currentSecsSinceEpoch()).toUtf8());
    for (const FileEntry &e : v) {
        f.write(e.dir ? "d\t" : "f\t");
        f.write(e.path.toUtf8());
        f.write("\n");
    }
    f.commit();
}

static bool skipName(const QString &n) {
    if (n.startsWith(QLatin1Char('.'))) return true;
    static const QSet<QString> skip = { QStringLiteral("node_modules"), QStringLiteral("__pycache__"), QStringLiteral("target"),
                                        QStringLiteral("build"), QStringLiteral("venv"), QStringLiteral("site-packages"), QStringLiteral("Trash") };
    return skip.contains(n);
}

bool FileIndex::rebuild(const CancelToken &cancel) {
    QElapsedTimer clock;
    clock.start();
    auto out = std::make_shared<QVector<FileEntry>>();
    QSet<QString> seenDirs;
    struct Item { QString path; int depth; };
    QList<Item> queue;   // breadth first, so shallow files come before deep ones if the bounds hit
    for (const QString &r : m_roots) queue.append({ QDir(r).absolutePath(), 0 });
    bool bounded = false;
    for (int qi = 0; qi < queue.size(); ++qi) {
        if (cancel.cancelled()) return false;
        if (clock.elapsed() > m_lim.budgetMs || out->size() >= m_lim.maxEntries) { bounded = true; break; }
        const Item it = queue[qi];
        const QString canon = QFileInfo(it.path).canonicalFilePath();
        if (canon.isEmpty() || seenDirs.contains(canon)) continue;   // a root inside another root, or a symlink loop
        seenDirs.insert(canon);
        QDirIterator di(it.path, QDir::AllEntries | QDir::NoDotAndDotDot | QDir::Hidden);
        while (di.hasNext()) {
            di.next();
            const QFileInfo fi = di.fileInfo();
            const QString name = fi.fileName();
            if (skipName(name)) continue;
            FileEntry e;
            e.path = fi.absoluteFilePath();
            e.name = name;
            e.lname = name.toLower();
            e.dir = fi.isDir();
            out->append(e);
            if (e.dir && !fi.isSymLink() && it.depth + 1 < m_lim.maxDepth) queue.append({ e.path, it.depth + 1 });
            if (out->size() >= m_lim.maxEntries) break;
        }
    }
    (void)bounded;
    saveCache(*out);
    QMutexLocker l(&m_mu);
    m_entries = out;
    m_at = QDateTime::currentSecsSinceEpoch();
    return true;
}

}  // namespace taos::search
