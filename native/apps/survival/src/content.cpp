// SPDX-License-Identifier: AGPL-3.0-or-later
#include "content.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QProcess>
#include <QStorageInfo>
#include <QUrl>

#include "paths.h"

namespace Taos::Survival {

using namespace Qt::StringLiterals;

namespace {
QVariantMap packView(const PackInfo &p, PacksEngine *packs)
{
    QVariantMap m = packs->pack(p.id);
    if (m.value(u"id"_s).toString().isEmpty()) {   // not known to the manager yet: show it as available
        m[u"id"_s] = p.id;
        m[u"state"_s] = u"available"_s;
        m[u"installed"_s] = false;
        m[u"progress"_s] = 0.0;
    }
    m[u"title"_s] = p.title;
    m[u"description"_s] = p.description;
    m[u"sizeText"_s] = sizeText(p.bytes);
    m[u"approxSize"_s] = p.sizeApprox;
    m[u"licence"_s] = p.licence;
    m[u"licenceUrl"_s] = p.licenceUrl;
    m[u"restrictive"_s] = p.needsAcceptance;
    m[u"source"_s] = p.source;
    m[u"version"_s] = p.version;
    m[u"language"_s] = p.language;
    m[u"series"_s] = p.series;
    return m;
}
bool allowFile() { return qEnvironmentVariableIsSet("TAOS_SURVIVAL_ALLOW_FILE_URLS"); }
QUrl overridableUrl(const char *env, const QString &fallback)
{
    const QUrl u(qEnvironmentVariable(env));
    // the override exists for tests only, and only for local files
    if (allowFile() && u.scheme() == u"file"_s)
        return u;
    return QUrl(fallback);
}
} // namespace

// ---- Kiwix catalogue ---------------------------------------------------------------------------------------------
CatalogEngine::CatalogEngine(PacksEngine *packs, QObject *parent) : QObject(parent), m_packs(packs)
{
    const QUrl base = overridableUrl("TAOS_SURVIVAL_CATALOG_URL", u"https://opds.library.kiwix.org/catalog/v2"_s);
    m_catalog.setBaseUrl(base);
    m_updates.setBaseUrl(base);
    connect(&m_catalog, &KiwixCatalog::searchDone, this, [this](int id, const CatalogPage &page) {
        if (id != m_id) return;
        m_entries = page.entries;
        m_total = page.total;
        m_start = page.start;
        m_loading = false;
        m_error = page.entries.isEmpty() && page.dropped > 0 ? tr("Nothing on this page could be offered: the entries had no https download.") : QString();
        Q_EMIT resultsChanged();
    });
    connect(&m_catalog, &KiwixCatalog::searchFailed, this, [this](int id, const QString &msg) {
        if (id != m_id) return;
        m_loading = false;
        m_error = msg;
        Q_EMIT resultsChanged();
    });
    connect(&m_catalog, &KiwixCatalog::categories, this, [this](const QList<CatalogFacet> &l) {
        m_cats.clear();
        for (const CatalogFacet &f : l) m_cats.append(QVariantMap{{u"id"_s, f.id}, {u"title"_s, f.title}});
        Q_EMIT facetsChanged();
    });
    connect(&m_catalog, &KiwixCatalog::languages, this, [this](const QList<CatalogFacet> &l) {
        m_langs.clear();
        for (const CatalogFacet &f : l) m_langs.append(QVariantMap{{u"id"_s, f.id}, {u"title"_s, f.title}});
        Q_EMIT facetsChanged();
    });
    connect(&m_catalog, &KiwixCatalog::facetsFailed, this, [this](const QString &) { Q_EMIT facetsChanged(); });
    connect(&m_updates, &KiwixCatalog::searchDone, this, [this](int id, const CatalogPage &page) {
        if (id != m_updateId) return;
        for (const CatalogEntry &e : page.entries)
            for (const PackInfo &have : m_packs->manager()->packs())
                if (have.installed && !have.series.isEmpty() && have.series == e.series() && e.version() > have.version)
                    m_found.append(packFromEntry(e));
        nextUpdateQuery();
    });
    connect(&m_updates, &KiwixCatalog::searchFailed, this, [this](int, const QString &msg) {
        m_checking = false;
        m_updateNote = tr("Updates could not be checked: %1").arg(msg);
        Q_EMIT updatesChanged();
    });
    connect(packs, &PacksEngine::changed, this, [this] { if (!m_entries.isEmpty()) Q_EMIT resultsChanged(); });
}

void CatalogEngine::loadFacets() { m_catalog.loadFacets(); }

void CatalogEngine::search(const QString &query, const QString &language, const QString &category, int start)
{
    m_q = query; m_lang = language; m_cat = category;
    m_loading = true;
    m_error.clear();
    Q_EMIT resultsChanged();
    m_id = m_catalog.search(query, language, category, start, 30);
}

QVariantList CatalogEngine::results() const
{
    QVariantList out;
    for (const CatalogEntry &e : m_entries) {
        const PackInfo p = packFromEntry(e);
        QVariantMap m = packView(p, m_packs);
        m[u"flavour"_s] = e.flavour;
        m[u"category"_s] = e.category;
        m[u"articlesText"_s] = e.articles > 0 ? QLocale::system().toString(e.articles) : QString();
        m[u"pictures"_s] = e.pictures();
        m[u"fullText"_s] = e.fullText();
        out.append(m);
    }
    return out;
}

QString CatalogEngine::install(const QString &packId)
{
    for (const CatalogEntry &e : std::as_const(m_entries)) {
        const PackInfo p = packFromEntry(e);
        if (p.id != packId)
            continue;
        m_packs->manager()->addRemote(p);
        if (p.needsAcceptance)
            m_packs->acceptLicence(p.id);   // the window calls this only after the person pressed Accept
        return m_packs->download(p.id) ? QString() : m_packs->message();
    }
    return tr("That pack is no longer in the list. Search again.");
}

void CatalogEngine::checkUpdates()
{
    if (m_checking) return;
    m_pendingNames.clear();
    m_found.clear();
    for (const PackInfo &p : m_packs->manager()->packs())
        if (p.installed && !p.series.isEmpty()) {
            const QString n = catalogNameOfSeries(p.series);
            if (!m_pendingNames.contains(n)) m_pendingNames << n;
        }
    if (m_pendingNames.isEmpty()) {
        m_updateNote = tr("No pack from the Kiwix catalogue is installed, so there is nothing to update.");
        Q_EMIT updatesChanged();
        return;
    }
    m_checking = true;
    m_updateNote = tr("Checking the catalogue...");
    Q_EMIT updatesChanged();
    nextUpdateQuery();
}

void CatalogEngine::nextUpdateQuery()
{
    if (m_pendingNames.isEmpty()) {
        m_checking = false;
        m_packs->manager()->setLatest(m_found);
        m_updateNote = m_found.isEmpty() ? tr("Everything is up to date.") : (m_found.size() == 1 ? tr("1 update available.") : tr("%1 updates available.").arg(m_found.size()));
        Q_EMIT updatesChanged();
        return;
    }
    m_updateId = m_updates.search(QString(), QString(), QString(), 0, 100, m_pendingNames.takeFirst());
}

// ---- curated collections -----------------------------------------------------------------------------------------
CollectionsEngine::CollectionsEngine(PacksEngine *packs, QObject *parent) : QObject(parent), m_packs(packs)
{
    const QJsonObject root = QJsonDocument::fromJson(bundledCatalog(u"collections.json"_s)).object();
    for (const QJsonValue &cv : root.value(u"categories"_s).toArray()) {
        const QJsonObject c = cv.toObject();
        QVariantList tiers;
        for (const QJsonValue &tv : c.value(u"tiers"_s).toArray()) {
            const QJsonObject t = tv.toObject();
            const QString slug = t.value(u"slug"_s).toString();
            QList<PackInfo> own;
            for (const QJsonValue &rv : t.value(u"resources"_s).toArray()) {
                const QJsonObject r = rv.toObject();
                const QUrl url(r.value(u"url"_s).toString());
                if (url.scheme() != u"https"_s || !url.fileName().endsWith(u".zim"_s))
                    continue;
                PackInfo p;
                p.id = u"kiwix:"_s + url.fileName().chopped(4);
                p.title = r.value(u"title"_s).toString();
                p.description = r.value(u"description"_s).toString();
                p.kind = u"zim"_s;
                p.file = url.fileName();
                p.url = url;
                p.sha256Url = QUrl(url.toString() + u".sha256"_s);
                p.bytes = qint64(r.value(u"sizeMb"_s).toDouble() * 1000000.0);
                p.sizeApprox = true;
                p.series = seriesOfFile(p.file);
                p.version = versionOfFile(p.file);
                p.source = url.host();
                p.publisher = u"Kiwix"_s;
                const LicenceInfo l = classifyLicence(p.series, QString());
                p.licence = l.licence; p.licenceUrl = l.url; p.needsAcceptance = l.restrictive;
                p.attribution = p.title + u", packaged by Kiwix. Licence: "_s + l.licence;
                own.append(p);
            }
            m_own.insert(slug, own);
            m_includes.insert(slug, t.value(u"includes"_s).toString());
            tiers.append(QVariantMap{{u"slug"_s, slug}, {u"name"_s, t.value(u"name"_s).toString()}, {u"description"_s, t.value(u"description"_s).toString()},
                                     {u"includes"_s, t.value(u"includes"_s).toString()}, {u"recommended"_s, t.value(u"recommended"_s).toBool()}});
        }
        m_view.append(QVariantMap{{u"slug"_s, c.value(u"slug"_s).toString()}, {u"name"_s, c.value(u"name"_s).toString()},
                                  {u"description"_s, c.value(u"description"_s).toString()}, {u"tiers"_s, tiers}});
    }
}

QList<PackInfo> CollectionsEngine::resolve(const QString &slug) const
{
    QList<PackInfo> out;
    QSet<QString> seen;
    QString cur = slug;
    while (!cur.isEmpty() && m_own.contains(cur)) {
        for (const PackInfo &p : m_own.value(cur))
            if (!seen.contains(p.id)) { seen.insert(p.id); out.append(p); }
        cur = m_includes.value(cur);
    }
    return out;
}

QVariantMap CollectionsEngine::tier(const QString &slug) const
{
    QVariantList list;
    qint64 remaining = 0;
    int restrictive = 0, installed = 0;
    for (const PackInfo &p : resolve(slug)) {
        QVariantMap v = packView(p, m_packs);
        if (v.value(u"installed"_s).toBool()) ++installed;
        else { remaining += p.bytes; if (p.needsAcceptance && !m_packs->manager()->licenceAccepted(p.id)) ++restrictive; }
        list.append(v);
    }
    return {{u"resources"_s, list}, {u"count"_s, int(list.size())}, {u"installed"_s, installed}, {u"remainingText"_s, sizeText(remaining)},
            {u"restrictiveCount"_s, restrictive}};
}

QString CollectionsEngine::installTier(const QString &slug, bool acceptRestrictive)
{
    QStringList ids;
    int skipped = 0;
    for (const PackInfo &p : resolve(slug)) {
        if (m_packs->manager()->pack(p.id).installed) continue;
        m_packs->manager()->addRemote(p);
        if (p.needsAcceptance) {
            if (!acceptRestrictive && !m_packs->manager()->licenceAccepted(p.id)) { ++skipped; continue; }
            m_packs->acceptLicence(p.id);
        }
        ids << p.id;
    }
    m_packs->enqueue(ids);
    if (skipped > 0)
        return (skipped == 1 ? tr("1 pack was left out because its licence was not accepted.") : tr("%1 packs were left out because their licences were not accepted.").arg(skipped));
    return ids.isEmpty() ? tr("Everything in this tier is already installed.") : QString();
}

// ---- maps by region ----------------------------------------------------------------------------------------------
MapPacksEngine::MapPacksEngine(PacksEngine *packs, ContainerExec *exec, QObject *parent)
    : QObject(parent), m_packs(packs), m_extractor(exec)
{
    m_all = loadMapRegions(bundledCatalog(u"regions.json"_s));
    for (const MapRegion &r : std::as_const(m_all)) {
        if (r.url.isEmpty())
            m_regions.append(QVariantMap{{u"id"_s, r.id}, {u"title"_s, r.title}, {u"group"_s, r.group},
                {u"box"_s, u"%1, %2 to %3, %4"_s.arg(r.west, 0, 'f', 1).arg(r.south, 0, 'f', 1).arg(r.east, 0, 'f', 1).arg(r.north, 0, 'f', 1)}});
        else
            m_ready.append(QVariantMap{{u"id"_s, u"map:"_s + r.id}, {u"title"_s, r.title}, {u"group"_s, r.group}, {u"sizeText"_s, sizeText(r.bytes)}});
    }
    connect(packs, &PacksEngine::changed, this, [this] { Q_EMIT changed(); });
    connect(&m_extractor, &MapExtractor::finished, this, [this](bool ok, bool dry, qint64 bytes, const QString &msg) {
        m_busy = false;
        if (!ok) { m_error = msg; m_status.clear(); Q_EMIT changed(); return; }
        if (dry) {
            m_estimate = bytes;
            m_status = tr("This area comes to about %1.").arg(sizeText(bytes));
            Q_EMIT changed();
            return;
        }
        PackInfo p;
        p.id = m_outId;
        p.title = m_outTitle;
        p.kind = u"map"_s;
        p.file = QFileInfo(m_pendingFile).fileName();
        p.licence = u"ODbL-1.0"_s;
        p.licenceUrl = u"https://www.openstreetmap.org/copyright"_s;
        p.attribution = tr("Map data (c) OpenStreetMap contributors (ODbL). Basemap by Protomaps.");
        p.source = u"build.protomaps.com"_s;
        p.series = u"protomaps-"_s + m_outId;
        p.version = m_pendingBuild;
        QString err;
        if (!m_packs->manager()->adoptFile(p, &err)) m_error = err; else m_status = tr("The map is ready: %1.").arg(sizeText(bytes));
        Q_EMIT changed();
    });
}

void MapPacksEngine::withBuild(std::function<void(const QString &)> then)
{
    const QUrl u = overridableUrl("TAOS_SURVIVAL_BUILDS_URL", u"https://build-metadata.protomaps.dev/builds.json"_s);
    QNetworkRequest req(u);
    req.setTransferTimeout(30 * 1000);
    QNetworkReply *r = m_nam.get(req);
    connect(r, &QNetworkReply::finished, this, [this, r, then] {
        const QUrl build = latestProtomapsBuild(r->read(4 * 1024 * 1024));
        const bool ok = r->error() == QNetworkReply::NoError && !build.isEmpty();
        r->deleteLater();
        if (!ok) { m_busy = false; m_error = tr("The newest map build could not be found. Check your connection."); Q_EMIT changed(); return; }
        then(build.toString());
    });
}

void MapPacksEngine::run(const QString &regionId, int maxZoom, bool dry)
{
    if (m_busy) return;
    const MapRegion *reg = nullptr;
    for (const MapRegion &r : std::as_const(m_all)) if (r.id == regionId && r.url.isEmpty()) reg = &r;
    if (!reg) { m_error = tr("Unknown map area."); Q_EMIT changed(); return; }
    m_busy = true;
    m_error.clear();
    m_status = dry ? tr("Measuring the area...") : tr("Cutting the map out. This reads only the part of the world you chose.");
    Q_EMIT changed();
    const MapRegion region = *reg;
    withBuild([this, region, maxZoom, dry](const QString &build) {
        const QString key = QUrl(build).fileName().chopped(8);   // 20261120
        m_pendingBuild = key;
        m_outId = u"map:extract-"_s + region.id + u"-z"_s + QString::number(clampMaxZoom(maxZoom));
        m_outTitle = u"%1 (zoom %2)"_s.arg(region.title).arg(clampMaxZoom(maxZoom));
        m_pendingFile = m_packs->manager()->packsDir() + u"/protomaps-"_s + region.id + u"-z"_s + QString::number(clampMaxZoom(maxZoom)) + u"-"_s + key + u".pmtiles"_s;
        if (!dry && m_estimate > 0 && QStorageInfo(m_packs->manager()->packsDir()).bytesAvailable() < m_estimate + (qint64(1) << 30)) {
            m_busy = false; m_error = tr("Not enough free space for this map."); Q_EMIT changed(); return;
        }
        QString err;
        if (!m_extractor.start(build, region, maxZoom, m_pendingFile, dry, &err)) { m_busy = false; m_error = err; Q_EMIT changed(); }
    });
}

void MapPacksEngine::estimate(const QString &regionId, int maxZoom) { m_estimate = 0; run(regionId, maxZoom, true); }
void MapPacksEngine::extract(const QString &regionId, int maxZoom) { run(regionId, maxZoom, false); }
void MapPacksEngine::cancel() { m_extractor.cancel(); }

QString MapPacksEngine::installReady(const QString &id)
{
    for (const MapRegion &r : std::as_const(m_all)) {
        if (u"map:"_s + r.id != id || r.url.isEmpty()) continue;
        PackInfo p;
        p.id = id;
        p.title = r.title + u" ("_s + r.group + u")"_s;
        p.kind = u"map"_s;
        p.file = r.url.fileName();
        p.url = r.url;
        p.bytes = r.bytes;
        p.sizeApprox = r.sizeApprox;
        p.noChecksum = true;   // the maps repository publishes no checksum: the size is checked and the pack says so
        p.licence = u"ODbL-1.0"_s;
        p.licenceUrl = u"https://www.openstreetmap.org/copyright"_s;
        p.attribution = tr("Map data (c) OpenStreetMap contributors (ODbL). Basemap by Protomaps.");
        p.source = r.url.host();
        p.series = seriesOfFile(r.url.fileName().replace(u".pmtiles"_s, u".zim"_s));
        p.version = r.version;
        m_packs->manager()->addRemote(p);
        return m_packs->download(id) ? QString() : m_packs->message();
    }
    return tr("Unknown map file.");
}

// ---- education ---------------------------------------------------------------------------------------------------
EduEngine::EduEngine(const QString &dataDir, ContainerExec *exec, QObject *parent)
    : QObject(parent), m_dataDir(dataDir), m_importer(exec)
{
    QFile f(m_dataDir + u"/education.json"_s);
    if (f.open(QIODevice::ReadOnly))
        for (const QJsonValue &v : QJsonDocument::fromJson(f.readAll()).object().value(u"installed"_s).toArray())
            m_installed << v.toString();
    connect(&m_importer, &ChannelImporter::finished, this, [this](const QString &id, bool ok, const QString &msg) {
        m_importing.clear();
        if (ok) { if (!m_installed.contains(id)) m_installed << id; saveInstalled(); m_message = tr("The channel is installed in Kolibri."); }
        else m_message = msg;
        Q_EMIT changed();
    });
}

void EduEngine::saveInstalled()
{
    QDir().mkpath(m_dataDir);
    QFile f(m_dataDir + u"/education.json"_s);
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(QJsonObject{{u"installed"_s, QJsonArray::fromStringList(m_installed)}}).toJson());
}

void EduEngine::load()
{
    if (m_loading) return;
    m_loading = true;
    m_error.clear();
    Q_EMIT changed();
    QNetworkRequest req(overridableUrl("TAOS_SURVIVAL_KOLIBRI_URL", u"https://kolibri-catalog-en.learningequality.org/api/public/v1/channels/?format=json"_s));
    req.setTransferTimeout(30 * 1000);
    QNetworkReply *r = m_nam.get(req);
    connect(r, &QNetworkReply::finished, this, [this, r] {
        const QByteArray data = r->read(32 * 1024 * 1024);
        const bool ok = r->error() == QNetworkReply::NoError;
        r->deleteLater();
        m_loading = false;
        if (!ok) { m_error = tr("The education catalogue could not be reached."); Q_EMIT changed(); return; }
        m_channels = parseKolibriChannels(data);
        if (m_channels.isEmpty()) m_error = tr("The education catalogue answered with nothing usable.");
        Q_EMIT changed();
    });
}

QVariantList EduEngine::channels() const
{
    QVariantList out;
    for (const EduChannel &c : m_channels)
        out.append(QVariantMap{{u"id"_s, c.id}, {u"name"_s, c.name}, {u"description"_s, c.description}, {u"language"_s, c.language},
            {u"sizeText"_s, sizeText(c.bytes)}, {u"resources"_s, c.resources}, {u"version"_s, c.version}, {u"lastPublished"_s, c.lastPublished},
            {u"installed"_s, m_installed.contains(c.id)}, {u"importing"_s, m_importing == c.id}});
    return out;
}

QString EduEngine::install(const QString &channelId, bool accepted)
{
    // channel licences are not in the catalogue: they are restrictive until the person has read them
    if (!accepted) return tr("Accept the channel's licence first.");
    QString err;
    if (!m_importer.start(channelId, &err)) return err;
    m_importing = channelId;
    m_message = tr("Kolibri is importing the channel. This can take a long time for large channels.");
    Q_EMIT changed();
    return QString();
}

// ---- models ------------------------------------------------------------------------------------------------------
ModelsEngine::ModelsEngine(QObject *parent) : QObject(parent)
{
    const QJsonObject o = QJsonDocument::fromJson(bundledCatalog(u"models.json"_s)).object();
    m_note = o.value(u"source"_s).toString();
    for (const QJsonValue &v : o.value(u"models"_s).toArray())
        m_models.append(v.toObject().toVariantMap());
}

bool ModelsEngine::openStore()
{
    return QProcess::startDetached(u"taos-store"_s, {u"--section"_s, u"models"_s});
}

} // namespace Taos::Survival
