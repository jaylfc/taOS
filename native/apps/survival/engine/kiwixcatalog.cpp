// SPDX-License-Identifier: AGPL-3.0-or-later
#include "kiwixcatalog.h"

#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QUrlQuery>
#include <QXmlStreamReader>

namespace Taos::Survival {

QString seriesOfFile(const QString &fileName)
{
    QString n = fileName;
    if (n.endsWith(QStringLiteral(".zim")))
        n.chop(4);
    static const QRegularExpression date(QStringLiteral("_\\d{4}-\\d{2}$"));
    n.remove(date);
    return n;
}
QString versionOfFile(const QString &fileName)
{
    static const QRegularExpression date(QStringLiteral("_(\\d{4}-\\d{2})\\.zim$"));
    const auto m = date.match(fileName);
    return m.hasMatch() ? m.captured(1) : QString();
}
QString catalogNameOfSeries(const QString &series)
{
    static const QStringList flavours{QStringLiteral("maxi"), QStringLiteral("mini"), QStringLiteral("nopic"), QStringLiteral("nodet"),
                                      QStringLiteral("novid"), QStringLiteral("blurred")};
    const qsizetype us = series.lastIndexOf(u'_');
    if (us > 0 && flavours.contains(series.mid(us + 1)))
        return series.left(us);
    return series;
}
QString CatalogEntry::series() const { return seriesOfFile(fileName()); }
QString CatalogEntry::version() const
{
    const QString v = versionOfFile(fileName());
    return v.isEmpty() ? updated.left(7) : v;
}

CatalogPage parseCatalogEntries(const QByteArray &xml)
{
    CatalogPage page;
    QXmlStreamReader r(xml);
    CatalogEntry cur;
    bool inEntry = false, inAuthor = false, inPublisher = false;
    bool haveLink = false;
    while (!r.atEnd()) {
        r.readNext();
        if (r.isStartElement()) {
            const QString n = r.name().toString();
            if (!inEntry) {
                if (n == QStringLiteral("totalResults")) page.total = r.readElementText().toInt();
                else if (n == QStringLiteral("startIndex")) page.start = r.readElementText().toInt();
                else if (n == QStringLiteral("entry")) { inEntry = true; cur = CatalogEntry(); haveLink = false; }
                continue;
            }
            if (n == QStringLiteral("author")) inAuthor = true;
            else if (n == QStringLiteral("publisher")) inPublisher = true;
            else if (n == QStringLiteral("name") && inPublisher) cur.publisher = r.readElementText();
            else if (n == QStringLiteral("name") && inAuthor) r.readElementText();
            else if (n == QStringLiteral("name")) cur.name = r.readElementText();
            else if (n == QStringLiteral("title")) cur.title = r.readElementText();
            else if (n == QStringLiteral("summary")) cur.summary = r.readElementText();
            else if (n == QStringLiteral("language")) cur.language = r.readElementText();
            else if (n == QStringLiteral("flavour")) cur.flavour = r.readElementText();
            else if (n == QStringLiteral("category")) cur.category = r.readElementText();
            else if (n == QStringLiteral("tags")) cur.tags = r.readElementText();
            else if (n == QStringLiteral("updated")) cur.updated = r.readElementText();
            else if (n == QStringLiteral("id")) cur.uuid = r.readElementText().remove(QStringLiteral("urn:uuid:"));
            else if (n == QStringLiteral("articleCount")) cur.articles = r.readElementText().toLongLong();
            else if (n == QStringLiteral("mediaCount")) cur.media = r.readElementText().toLongLong();
            else if (n == QStringLiteral("link") && r.attributes().value(QStringLiteral("type")) == QStringLiteral("application/x-zim")) {
                QString href = r.attributes().value(QStringLiteral("href")).toString();
                if (href.endsWith(QStringLiteral(".meta4")))
                    href.chop(6);
                const QUrl u(href);
                // https only: an entry that offers any other scheme (or none) is left out, never "fixed"
                if (u.scheme() == QStringLiteral("https") && !u.host().isEmpty() && u.fileName().endsWith(QStringLiteral(".zim"))) {
                    cur.zimUrl = u;
                    cur.bytes = r.attributes().value(QStringLiteral("length")).toLongLong();
                    haveLink = true;
                }
            }
        } else if (r.isEndElement()) {
            const QString n = r.name().toString();
            if (n == QStringLiteral("author")) inAuthor = false;
            else if (n == QStringLiteral("publisher")) inPublisher = false;
            else if (n == QStringLiteral("entry") && inEntry) {
                inEntry = false;
                if (haveLink && cur.bytes > 0 && !cur.name.isEmpty()) page.entries.append(cur);
                else ++page.dropped;
            }
        }
    }
    return page;
}

QList<CatalogFacet> parseCatalogFacets(const QByteArray &xml)
{
    QList<CatalogFacet> out;
    QXmlStreamReader r(xml);
    bool inEntry = false;
    CatalogFacet cur;
    while (!r.atEnd()) {
        r.readNext();
        if (r.isStartElement()) {
            const QString n = r.name().toString();
            if (n == QStringLiteral("entry")) { inEntry = true; cur = CatalogFacet(); }
            else if (inEntry && n == QStringLiteral("title")) cur.title = r.readElementText();
            else if (inEntry && n == QStringLiteral("language")) cur.id = r.readElementText();
            else if (inEntry && n == QStringLiteral("link")) {
                const QString href = r.attributes().value(QStringLiteral("href")).toString();
                const qsizetype i = href.indexOf(QStringLiteral("category="));
                if (i >= 0) cur.id = QUrl::fromPercentEncoding(href.mid(i + 9).toUtf8());
            }
        } else if (r.isEndElement() && r.name() == QStringLiteral("entry") && inEntry) {
            inEntry = false;
            if (cur.id.isEmpty() && !cur.title.isEmpty()) cur.id = cur.title;
            if (!cur.id.isEmpty()) out.append(cur);
        }
    }
    return out;
}

QUrl catalogEntriesUrl(const QUrl &base, const QString &query, const QString &language, const QString &category, const QString &name, int start, int count)
{
    QUrl u(base.toString() + QStringLiteral("/entries"));
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("start"), QString::number(qMax(0, start)));
    q.addQueryItem(QStringLiteral("count"), QString::number(qBound(1, count, 100)));
    if (!query.trimmed().isEmpty()) q.addQueryItem(QStringLiteral("q"), query.trimmed());
    if (!language.isEmpty()) q.addQueryItem(QStringLiteral("lang"), language);
    if (!category.isEmpty()) q.addQueryItem(QStringLiteral("category"), category);
    if (!name.isEmpty()) q.addQueryItem(QStringLiteral("name"), name);
    u.setQuery(q);
    return u;
}

LicenceInfo classifyLicence(const QString &series, const QString &category)
{
    auto has = [&](const char *s) { return series.startsWith(QLatin1String(s)); };
    LicenceInfo l;
    l.known = true;
    const QString ccbysa = QStringLiteral("https://creativecommons.org/licenses/by-sa/4.0/");
    if (has("wikipedia_") || has("wikibooks_") || has("wikiversity_") || has("wikivoyage_") || has("wiktionary_") || has("wikiquote_")
        || has("wikisource_") || has("wikinews_") || has("wikispecies_") || has("wikem_") || has("librepathology_")) {
        l.licence = QStringLiteral("CC-BY-SA-4.0"); l.url = ccbysa;
    } else if (series.contains(QStringLiteral(".stackexchange.com_")) || series.contains(QStringLiteral("stackoverflow"))) {
        l.licence = QStringLiteral("CC-BY-SA-4.0"); l.url = ccbysa;
    } else if (has("gutenberg_") || category == QStringLiteral("gutenberg")) {
        l.licence = QStringLiteral("Public domain (US)"); l.url = QStringLiteral("https://www.gutenberg.org/policy/permission.html");
    } else if (has("wwwnc.cdc.gov") || has("medlineplus.gov") || has("www.ready.gov") || has("www.fema.gov")) {
        l.licence = QStringLiteral("US government work (public domain)");
    } else if (has("libretexts.org")) {
        l.licence = QStringLiteral("CC-BY-NC-SA-4.0"); l.url = QStringLiteral("https://creativecommons.org/licenses/by-nc-sa/4.0/"); l.restrictive = true;
    } else if (has("ifixit_") || category == QStringLiteral("iFixit")) {
        l.licence = QStringLiteral("CC-BY-NC-SA-3.0"); l.url = QStringLiteral("https://creativecommons.org/licenses/by-nc-sa/3.0/"); l.restrictive = true;
    } else if (has("ted_")) {
        l.licence = QStringLiteral("CC-BY-NC-ND-4.0"); l.url = QStringLiteral("https://creativecommons.org/licenses/by-nc-nd/4.0/"); l.restrictive = true;
    } else if (has("100r.co")) {
        l.licence = QStringLiteral("CC-BY-NC-SA-4.0"); l.url = QStringLiteral("https://creativecommons.org/licenses/by-nc-sa/4.0/"); l.restrictive = true;
    } else if (has("phet_")) {
        l.licence = QStringLiteral("CC-BY-4.0"); l.url = QStringLiteral("https://creativecommons.org/licenses/by/4.0/");
    } else {
        // not known: it may be free, it may not. The pack's own licence (in the file) must be read, so ask first.
        l.known = false; l.restrictive = true;
        l.licence = QStringLiteral("Unknown: see the pack");
    }
    return l;
}

PackInfo packFromEntry(const CatalogEntry &e)
{
    PackInfo p;
    const QString file = e.fileName();
    p.id = QStringLiteral("kiwix:") + file.left(file.size() - 4);
    p.title = e.flavour.isEmpty() ? e.title : e.title + QStringLiteral(" (") + e.flavour + QLatin1Char(')');
    p.description = e.summary;
    p.kind = QStringLiteral("zim");
    p.language = e.language;
    p.file = file;
    p.url = e.zimUrl;
    p.sha256Url = QUrl(e.zimUrl.toString() + QStringLiteral(".sha256"));
    p.bytes = e.bytes;
    p.series = e.series();
    p.version = e.version();
    p.source = e.zimUrl.host().replace(QStringLiteral("lb."), QString());
    p.publisher = e.publisher.isEmpty() ? QStringLiteral("Kiwix") : e.publisher;
    const LicenceInfo l = classifyLicence(p.series, e.category);
    p.licence = l.licence;
    p.licenceUrl = l.url;
    p.needsAcceptance = l.restrictive;
    p.attribution = e.title + QStringLiteral(", packaged by ") + p.publisher + QStringLiteral(" for Kiwix. Licence: ") + l.licence;
    return p;
}

KiwixCatalog::KiwixCatalog(QObject *parent) : QObject(parent)
{
    qRegisterMetaType<Taos::Survival::CatalogPage>();
    qRegisterMetaType<QList<Taos::Survival::CatalogFacet>>();
}

QByteArray KiwixCatalog::fetchLimited(QNetworkReply *r)
{
    return r->read(kMaxFeedBytes + 1);
}

int KiwixCatalog::search(const QString &query, const QString &language, const QString &category, int start, int count, const QString &name)
{
    if (m_current) { m_current->abort(); m_current = nullptr; }   // the newest search wins
    const int id = ++m_next;
    const QUrl u = catalogEntriesUrl(m_base, query, language, category, name, start, count);
    QNetworkRequest req(u);
    req.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
    req.setTransferTimeout(30 * 1000);
    QNetworkReply *r = m_nam.get(req);
    m_current = r;
    auto *buf = new QByteArray;
    connect(r, &QNetworkReply::readyRead, r, [this, r, buf] {
        buf->append(fetchLimited(r));
        if (buf->size() > kMaxFeedBytes) r->abort();   // a feed bigger than this is not a catalogue page
    });
    connect(r, &QNetworkReply::finished, this, [this, r, id, buf] {
        if (m_current == r) m_current = nullptr;
        buf->append(r->readAll());
        const bool tooBig = buf->size() > kMaxFeedBytes;
        const QNetworkReply::NetworkError e = r->error();
        const QString es = r->errorString();
        r->deleteLater();
        const QByteArray data = *buf;
        delete buf;
        if (e == QNetworkReply::OperationCanceledError && !tooBig) return;   // replaced by a newer search
        if (tooBig) { Q_EMIT searchFailed(id, tr("The catalogue answered with far more data than a page should hold, so it was ignored.")); return; }
        if (e != QNetworkReply::NoError) { Q_EMIT searchFailed(id, tr("The Kiwix catalogue could not be reached: %1").arg(es)); return; }
        const CatalogPage page = parseCatalogEntries(data);
        if (page.entries.isEmpty() && page.total == 0 && !data.contains("<feed"))
            Q_EMIT searchFailed(id, tr("The catalogue answered with something that is not a catalogue feed."));
        else
            Q_EMIT searchDone(id, page);
    });
    return id;
}

void KiwixCatalog::loadFacets()
{
    for (const char *kind : {"categories", "languages"}) {
        QNetworkRequest req(QUrl(m_base.toString() + QLatin1Char('/') + QLatin1String(kind)));
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
        req.setTransferTimeout(30 * 1000);
        QNetworkReply *r = m_nam.get(req);
        const bool cats = QLatin1String(kind) == QLatin1String("categories");
        connect(r, &QNetworkReply::finished, this, [this, r, cats] {
            const QByteArray data = r->read(kMaxFeedBytes);
            const bool ok = r->error() == QNetworkReply::NoError;
            r->deleteLater();
            if (!ok) { Q_EMIT facetsFailed(tr("The category and language lists could not be loaded.")); return; }
            const auto list = parseCatalogFacets(data);
            if (cats) Q_EMIT categories(list); else Q_EMIT languages(list);
        });
    }
}

} // namespace Taos::Survival
