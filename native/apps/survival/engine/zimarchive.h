// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Reads one ZIM archive (libzim, GPL-2.0-or-later) for the Library: metadata, title and full text search, and the bytes
// of an entry. It knows nothing of Qt Quick. Article HTML goes through articlehtml.h before anything shows it.
#include <QByteArray>
#include <QList>
#include <QString>
#include <memory>

namespace Taos::Survival {

struct ZimInfo
{
    QString title, description, language, creator, publisher, date, licence, uuid, mainPath;
    qint64 articleCount = 0;
    qint64 entryCount = 0;
    bool hasFulltext = false;
    bool hasTitleIndex = false;
};

struct ZimHit
{
    QString path;
    QString title;
    QString snippet;   // plain text, may be empty
};

struct ZimEntry
{
    bool found = false;
    QString path;      // the entry the redirects led to
    QString title;
    QString mimeType;
    QByteArray data;
};

class ZimArchive
{
public:
    ~ZimArchive();
    /// Opens `file`; on failure returns null and says why in `error`.
    static std::unique_ptr<ZimArchive> open(const QString &file, QString *error = nullptr);
    const ZimInfo &info() const { return m_info; }
    const QString &file() const { return m_file; }
    /// Title search (prefix and word matches) over the title index.
    QList<ZimHit> suggest(const QString &text, int limit = 20) const;
    /// Full text search; empty when the archive has no full text index (see info().hasFulltext).
    QList<ZimHit> fullText(const QString &text, int limit = 20) const;
    /// Full text where there is an index, titles otherwise.
    QList<ZimHit> search(const QString &text, int limit = 20) const;
    /// The entry at `path`, redirects followed. found=false when there is none.
    ZimEntry entry(const QString &path) const;
    QString randomPath() const;

private:
    ZimArchive();
    struct Impl;
    std::unique_ptr<Impl> d;
    ZimInfo m_info;
    QString m_file;
};

} // namespace Taos::Survival
