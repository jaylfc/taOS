// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The whole Kiwix library as a catalogue: the OPDS feed at opds.library.kiwix.org (every ZIM Kiwix publishes, in
// every language), parsed into entries a person can search, filter and install. Nothing is hand picked. Every
// address that comes out of a feed must be https; a feed that offers another scheme gets that entry dropped.
#include <QList>
#include <QNetworkAccessManager>
#include <QObject>
#include <QString>
#include <QUrl>

#include "packmanager.h"

namespace Taos::Survival {

struct CatalogEntry
{
    QString uuid, title, summary, language, name, flavour, category, publisher, updated, tags;
    qint64 articles = 0, media = 0, bytes = 0;
    QUrl zimUrl;              // the .zim itself (the .meta4 suffix of the feed link is removed)
    QString fileName() const { return zimUrl.fileName(); }
    QString series() const;   // file name without the date: wikipedia_en_all_nopic
    QString version() const;  // 2026-06 from the file name (the feed's date when the name has none)
    bool pictures() const { return tags.contains(QStringLiteral("_pictures:yes")); }
    bool fullText() const { return tags.contains(QStringLiteral("_ftindex:yes")); }
};

struct CatalogPage
{
    QList<CatalogEntry> entries;
    int total = 0;
    int start = 0;
    int dropped = 0;          // entries left out: no https link, or no link at all
};

struct CatalogFacet
{
    QString id;               // category name or ISO 639-3 code
    QString title;
};

struct LicenceInfo
{
    QString licence;          // SPDX id or a plain name
    QString url;
    bool restrictive = false; // needs the person's acceptance on first use
    bool known = false;       // false: guessed from nothing; the pack's own licence must be read
};

CatalogPage parseCatalogEntries(const QByteArray &xml);
QList<CatalogFacet> parseCatalogFacets(const QByteArray &xml);   // categories and languages feeds
QUrl catalogEntriesUrl(const QUrl &base, const QString &query, const QString &language, const QString &category, const QString &name, int start, int count);
LicenceInfo classifyLicence(const QString &series, const QString &category);
/// A pack the manager can download: the checksum comes from `<zim url>.sha256` when the download starts.
PackInfo packFromEntry(const CatalogEntry &e);
/// The series of a file name ("x_2026-06.zim" gives "x") and its date.
QString seriesOfFile(const QString &fileName);
QString versionOfFile(const QString &fileName);
/// The OPDS `name` to ask for when looking for newer versions of an installed series.
QString catalogNameOfSeries(const QString &series);

class KiwixCatalog : public QObject
{
    Q_OBJECT
public:
    explicit KiwixCatalog(QObject *parent = nullptr);
    void setBaseUrl(const QUrl &u) { m_base = u; }
    QUrl baseUrl() const { return m_base; }
    /// Starts a search. Returns an id; the result arrives with that id. An older search that is still running is dropped.
    int search(const QString &query, const QString &language, const QString &category, int start = 0, int count = 30, const QString &name = QString());
    void loadFacets();
    static constexpr qint64 kMaxFeedBytes = 8 * 1024 * 1024;
Q_SIGNALS:
    void searchDone(int id, const Taos::Survival::CatalogPage &page);
    void searchFailed(int id, const QString &message);
    void categories(const QList<Taos::Survival::CatalogFacet> &list);
    void languages(const QList<Taos::Survival::CatalogFacet> &list);
    void facetsFailed(const QString &message);
private:
    QByteArray fetchLimited(QNetworkReply *r);
    QNetworkAccessManager m_nam;
    QUrl m_base{QStringLiteral("https://opds.library.kiwix.org/catalog/v2")};
    int m_next = 0;
    QNetworkReply *m_current = nullptr;
};

} // namespace Taos::Survival
Q_DECLARE_METATYPE(Taos::Survival::CatalogPage)
Q_DECLARE_METATYPE(QList<Taos::Survival::CatalogFacet>)
