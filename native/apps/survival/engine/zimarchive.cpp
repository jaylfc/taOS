// SPDX-License-Identifier: AGPL-3.0-or-later
#include "zimarchive.h"

#include <zim/archive.h>
#include <zim/entry.h>
#include <zim/error.h>
#include <zim/item.h>
#include <zim/search.h>
#include <zim/suggestion.h>

#include <QRegularExpression>
#include <QSet>
#include <exception>

namespace Taos::Survival {

namespace {
QString q(const std::string &s) { return QString::fromUtf8(s.data(), qsizetype(s.size())); }
std::string s(const QString &v) { return v.toStdString(); }
QString plainSnippet(const std::string &html)
{
    // libzim snippets carry <b> marks around the matches; the Library shows plain text.
    QString t = q(html);
    t.remove(QRegularExpression(QStringLiteral("<[^>]*>")));
    t.replace(QStringLiteral("&amp;"), QStringLiteral("&")).replace(QStringLiteral("&lt;"), QStringLiteral("<"))
        .replace(QStringLiteral("&gt;"), QStringLiteral(">")).replace(QStringLiteral("&quot;"), QStringLiteral("\""));
    return t.simplified();
}
} // namespace

struct ZimArchive::Impl
{
    explicit Impl(const std::string &file) : archive(file) {}
    zim::Archive archive;
};

ZimArchive::ZimArchive() = default;
ZimArchive::~ZimArchive() = default;

std::unique_ptr<ZimArchive> ZimArchive::open(const QString &file, QString *error)
{
    std::unique_ptr<ZimArchive> a(new ZimArchive);
    try {
        a->d = std::make_unique<Impl>(file.toStdString());
        a->m_file = file;
        zim::Archive &z = a->d->archive;
        auto meta = [&z](const char *key) {
            try { return q(z.getMetadata(key)); } catch (const std::exception &) { return QString(); }
        };
        ZimInfo &i = a->m_info;
        i.title = meta("Title");
        i.description = meta("Description");
        i.language = meta("Language");
        i.creator = meta("Creator");
        i.publisher = meta("Publisher");
        i.date = meta("Date");
        i.licence = meta("License");
        i.uuid = q(std::string(z.getUuid()));
        i.articleCount = qint64(z.getArticleCount());
        i.entryCount = qint64(z.getEntryCount());
        i.hasFulltext = z.hasFulltextIndex();
        i.hasTitleIndex = z.hasTitleIndex();
        if (z.hasMainEntry()) {
            try { i.mainPath = q(z.getMainEntry().getItem(true).getPath()); } catch (const std::exception &) {}
        }
        return a;
    } catch (const std::exception &e) {
        if (error)
            *error = QString::fromUtf8(e.what());
        return nullptr;
    }
}

QList<ZimHit> ZimArchive::suggest(const QString &text, int limit) const
{
    QList<ZimHit> out;
    if (text.trimmed().isEmpty() || !d)
        return out;
    try {
        zim::SuggestionSearcher searcher(d->archive);
        auto result = searcher.suggest(s(text.trimmed()));
        for (const auto &r : result.getResults(0, limit))
            out.append({q(r.getPath()), q(r.getTitle()), plainSnippet(r.getSnippet())});
    } catch (const std::exception &) {
    }
    return out;
}

QList<ZimHit> ZimArchive::fullText(const QString &text, int limit) const
{
    QList<ZimHit> out;
    if (text.trimmed().isEmpty() || !d || !m_info.hasFulltext)
        return out;
    try {
        zim::Searcher searcher(d->archive);
        auto result = searcher.search(zim::Query(s(text.trimmed())));
        const auto rs = result.getResults(0, limit);
        for (auto it = rs.begin(); it != rs.end(); ++it)
            out.append({q(it.getPath()), q(it.getTitle()), plainSnippet(it.getSnippet())});
    } catch (const std::exception &) {
    }
    return out;
}

QList<ZimHit> ZimArchive::search(const QString &text, int limit) const
{
    // Titles first (what a person usually means), then full text hits that are not already there.
    QList<ZimHit> out = suggest(text, limit);
    QSet<QString> seen;
    for (const ZimHit &h : std::as_const(out))
        seen.insert(h.path);
    for (const ZimHit &h : fullText(text, limit)) {
        if (out.size() >= limit)
            break;
        if (!seen.contains(h.path))
            out.append(h);
    }
    return out;
}

ZimEntry ZimArchive::entry(const QString &path) const
{
    ZimEntry e;
    if (!d)
        return e;
    try {
        QString p = path;
        if (p.startsWith(QLatin1Char('/')))
            p.remove(0, 1);
        if (!d->archive.hasEntryByPath(s(p)))
            return e;
        zim::Item item = d->archive.getEntryByPath(s(p)).getItem(true);
        zim::Blob blob = item.getData();
        e.found = true;
        e.path = q(item.getPath());
        e.title = q(item.getTitle());
        e.mimeType = q(item.getMimetype());
        e.data = QByteArray(blob.data(), qsizetype(blob.size()));
    } catch (const std::exception &) {
        e = ZimEntry();
    }
    return e;
}

QString ZimArchive::randomPath() const
{
    if (!d)
        return {};
    try {
        return q(d->archive.getRandomEntry().getItem(true).getPath());
    } catch (const std::exception &) {
        return {};
    }
}

} // namespace Taos::Survival
