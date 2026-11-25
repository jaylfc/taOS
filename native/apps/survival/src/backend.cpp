// SPDX-License-Identifier: AGPL-3.0-or-later
#include "backend.h"

#include <QDir>
#include <QFile>
#include <QImage>
#include <QLocale>
#include <QQmlEngine>
#include <QStorageInfo>
#include <QUrl>
#include <QtConcurrent>
#include <cmath>

#include "articlehtml.h"
#include "content.h"
#include "kit.h"
#include "mapview.h"
#include "paths.h"

#ifdef TAOS_SURVIVAL_POSITIONING
#include <QGeoPositionInfo>
#include <QGeoPositionInfoSource>
#endif

namespace Taos::Survival {

using namespace Qt::StringLiterals;

QString sizeText(qint64 bytes)
{
    if (bytes <= 0)
        return QString();
    return QLocale::system().formattedDataSize(bytes, bytes >= 100'000'000 ? 0 : 1, QLocale::DataSizeSIFormat);
}

// ---- archives and images ----------------------------------------------------------------------------------------
ArchiveRegistry &ArchiveRegistry::instance()
{
    static ArchiveRegistry r;
    return r;
}
void ArchiveRegistry::set(const QString &id, std::shared_ptr<ZimArchive> a)
{
    QWriteLocker l(&m_lock);
    m_map.insert(id, std::move(a));
}
std::shared_ptr<ZimArchive> ArchiveRegistry::get(const QString &id) const
{
    QReadLocker l(&m_lock);
    return m_map.value(id);
}
void ArchiveRegistry::remove(const QString &id)
{
    QWriteLocker l(&m_lock);
    m_map.remove(id);
}

QImage ZimImageProvider::requestImage(const QString &id, QSize *size, const QSize &)
{
    const qsizetype slash = id.indexOf(u'/');
    if (slash <= 0)
        return {};
    const QString pack = QUrl::fromPercentEncoding(id.left(slash).toUtf8());
    const QString path = QUrl::fromPercentEncoding(id.mid(slash + 1).toUtf8());
    const auto zim = ArchiveRegistry::instance().get(pack);
    if (!zim)
        return {};
    const ZimEntry e = zim->entry(path);
    if (!e.found || !e.mimeType.startsWith(u"image/"_s))
        return {};
    QImage img = e.mimeType.startsWith(u"image/svg"_s) ? QImage::fromData(e.data, "svg") : QImage::fromData(e.data);
    if (img.isNull())
        return {};
    if (img.width() > 720)
        img = img.scaledToWidth(720, Qt::SmoothTransformation);
    if (size)
        *size = img.size();
    return img;
}

// ---- packs --------------------------------------------------------------------------------------------------------
PacksEngine::PacksEngine(const QString &dataDir, QObject *parent) : QObject(parent), m_pm(dataDir, this)
{
    QString err;
    m_pm.loadCatalogue(bundledCatalog(u"packs.json"_s), &err);
    auto *throttle = new QTimer(this);
    throttle->setSingleShot(true);
    throttle->setInterval(200);
    connect(throttle, &QTimer::timeout, this, &PacksEngine::changed);
    connect(&m_pm, &PackManager::changed, this, [throttle] { if (!throttle->isActive()) throttle->start(); });
    connect(&m_pm, &PackManager::progress, this, [this, throttle](const QString &id, qint64 got, qint64 total) {
        m_progress.insert(id, {got, total});
        if (!throttle->isActive()) throttle->start();
    });
    connect(&m_pm, &PackManager::finished, this, [this](const QString &id, bool ok, const QString &msg) {
        m_progress.remove(id);
        setMessage(ok ? tr("%1 is ready.").arg(m_pm.pack(id).title) : msg);
        if (ok) Q_EMIT packInstalled(id);
        if (id == m_active) { m_active.clear(); startNext(); }
        Q_EMIT changed();
    });
}

QVariantMap PacksEngine::toMap(const PackInfo &p) const
{
    const auto prog = m_progress.value(p.id);
    const qint64 total = prog.second > 0 ? prog.second : p.bytes;
    const qint64 got = prog.first > 0 ? prog.first : p.received;
    QVariantMap m;
    m[u"id"_s] = p.id; m[u"title"_s] = p.title; m[u"description"_s] = p.description; m[u"kind"_s] = p.kind;
    m[u"language"_s] = p.language; m[u"licence"_s] = p.licence; m[u"licenceUrl"_s] = p.licenceUrl; m[u"attribution"_s] = p.attribution;
    m[u"publisher"_s] = p.publisher; m[u"version"_s] = p.version; m[u"needsAcceptance"_s] = p.needsAcceptance;
    m[u"licenceAccepted"_s] = p.licenceAccepted; m[u"installed"_s] = p.installed; m[u"local"_s] = p.local;
    m[u"state"_s] = p.state; m[u"error"_s] = p.error; m[u"hasDownload"_s] = !p.url.isEmpty(); m[u"recipe"_s] = p.recipe;
    m[u"sizeText"_s] = sizeText(p.bytes); m[u"installedText"_s] = sizeText(p.installedBytes);
    m[u"receivedText"_s] = sizeText(got); m[u"path"_s] = p.path;
    m[u"progress"_s] = total > 0 ? qBound(0.0, double(got) / double(total), 1.0) : 0.0;
    m[u"resumable"_s] = got > 0 && p.state == u"available"_s;
    m[u"source"_s] = p.source; m[u"series"_s] = p.series; m[u"restrictive"_s] = p.needsAcceptance;
    m[u"unverified"_s] = p.unverified; m[u"updateAvailable"_s] = p.updateAvailable; m[u"updateVersion"_s] = p.updateVersion;
    m[u"catalogPack"_s] = !p.series.isEmpty();
    return m;
}

QVariantList PacksEngine::packs() const
{
    QVariantList out;
    for (const PackInfo &p : m_pm.packs())
        out.append(toMap(p));
    return out;
}
QVariantList PacksEngine::ofKind(const QString &kind, bool installedOnly) const
{
    QVariantList out;
    for (const PackInfo &p : m_pm.packs())
        if (p.kind == kind && (!installedOnly || p.installed))
            out.append(toMap(p));
    return out;
}
void PacksEngine::enqueue(const QStringList &ids)
{
    for (const QString &id : ids)
        if (!m_queue.contains(id) && id != m_active) m_queue << id;
    if (m_active.isEmpty())
        startNext();
}
void PacksEngine::startNext()
{
    while (!m_queue.isEmpty()) {
        const QString id = m_queue.takeFirst();
        QString err;
        if (m_pm.download(id, &err)) { m_active = id; return; }
        setMessage(err);
    }
    m_active.clear();
}
bool PacksEngine::update(const QString &id)
{
    QString err;
    if (!m_pm.update(id, &err)) { setMessage(err); return false; }
    setMessage(QString());
    return true;
}

QVariantMap PacksEngine::pack(const QString &id) const { return toMap(m_pm.pack(id)); }
QString PacksEngine::freeText() const
{
    return sizeText(QStorageInfo(m_pm.packsDir()).bytesAvailable());
}
bool PacksEngine::download(const QString &id)
{
    QString err;
    if (!m_pm.download(id, &err)) {
        setMessage(err);
        return false;
    }
    setMessage(QString());
    return true;
}
bool PacksEngine::remove(const QString &id)
{
    QString err;
    const bool ok = m_pm.remove(id, &err);
    if (ok) Q_EMIT packRemoved(id);
    setMessage(ok ? QString() : err);
    Q_EMIT changed();
    return ok;
}
QString PacksEngine::verify(const QString &id)
{
    switch (m_pm.verifyInstalled(id)) {
    case VerifyResult::Ok: return tr("The file matches its checksum.");
    case VerifyResult::Mismatch: return tr("The file does NOT match its checksum. Delete it and download again.");
    case VerifyResult::NoChecksum: return tr("There is no checksum to compare with for this pack.");
    case VerifyResult::Unreadable: return tr("The file could not be read.");
    }
    return {};
}
bool PacksEngine::importFile(const QString &path, const QString &sha256)
{
    QString p = path.trimmed();
    if (p.startsWith(u"file://"_s))
        p = QUrl(p).toLocalFile();
    QString err;
    const bool ok = m_pm.importFile(p, sha256, &err);
    setMessage(ok ? tr("Imported.") : err);
    Q_EMIT changed();
    return ok;
}

// ---- library ------------------------------------------------------------------------------------------------------
LibraryEngine::LibraryEngine(PacksEngine *packs, QObject *parent) : QObject(parent), m_packs(packs)
{
    connect(packs, &PacksEngine::packRemoved, this, [this](const QString &id) { if (id == m_packId) close(); });
}

bool LibraryEngine::open(const QString &packId)
{
    const PackInfo p = m_packs->manager()->pack(packId);
    if (!p.installed || p.kind != u"zim"_s) {
        m_error = tr("That pack is not installed.");
        Q_EMIT packChanged();
        return false;
    }
    QString err;
    std::shared_ptr<ZimArchive> z(ZimArchive::open(p.path, &err).release());
    if (!z) {
        m_error = tr("This pack could not be opened. %1").arg(err);
        Q_EMIT packChanged();
        return false;
    }
    if (!m_packId.isEmpty())
        ArchiveRegistry::instance().remove(m_packId);
    m_zim = z;
    m_packId = packId;
    ArchiveRegistry::instance().set(packId, z);
    const ZimInfo &i = z->info();
    m_info = {{u"title"_s, i.title.isEmpty() ? p.title : i.title}, {u"description"_s, i.description}, {u"language"_s, i.language},
              {u"date"_s, i.date}, {u"articles"_s, i.articleCount}, {u"fullText"_s, i.hasFulltext}, {u"titleIndex"_s, i.hasTitleIndex},
              {u"licence"_s, p.licence}, {u"attribution"_s, p.attribution}, {u"creator"_s, i.creator}};
    m_error.clear();
    m_results.clear();
    m_query.clear();
    m_history.clear();
    m_hpos = -1;
    m_article.clear();
    Q_EMIT packChanged();
    Q_EMIT resultsChanged();
    Q_EMIT articleChanged();
    home();
    return true;
}

void LibraryEngine::close()
{
    if (!m_packId.isEmpty())
        ArchiveRegistry::instance().remove(m_packId);
    m_zim.reset();
    m_packId.clear();
    m_info.clear();
    m_results.clear();
    m_article.clear();
    m_history.clear();
    m_hpos = -1;
    Q_EMIT packChanged();
    Q_EMIT resultsChanged();
    Q_EMIT articleChanged();
}

void LibraryEngine::search(const QString &text)
{
    m_query = text;
    m_results.clear();
    if (m_zim && !text.trimmed().isEmpty()) {
        for (const ZimHit &h : m_zim->search(text, 40))
            m_results.append(QVariantMap{{u"path"_s, h.path}, {u"title"_s, h.title.isEmpty() ? h.path : h.title}, {u"snippet"_s, h.snippet}});
    }
    Q_EMIT resultsChanged();
}

void LibraryEngine::show(const QString &path, bool record)
{
    if (!m_zim)
        return;
    // Reading the entry and rebuilding its HTML happen off the window's thread: a heavy article never freezes the app.
    // Only the newest request may show its result.
    const quint64 req = ++m_request;
    QVariantMap loading;
    loading[u"kind"_s] = u"loading"_s;
    loading[u"title"_s] = path;
    m_article = loading;
    Q_EMIT articleChanged();
    const std::shared_ptr<ZimArchive> zim = m_zim;
    const QString packId = m_packId;
    auto *w = new QFutureWatcher<QPair<ZimEntry, SanitizedArticle>>(this);
    connect(w, &QFutureWatcher<QPair<ZimEntry, SanitizedArticle>>::finished, this, [this, w, req, path, record] {
        const auto r = w->result();
        w->deleteLater();
        if (req != m_request)
            return;
        apply(path, record, path, r.first, r.second);
    });
    w->setFuture(QtConcurrent::run([zim, packId, path] {
        ZimEntry e = zim->entry(path);
        SanitizedArticle s;
        if (e.found && e.mimeType.startsWith(u"text/html"_s)) {
            if (e.data.size() > 8 * 1024 * 1024) {
                s.truncated = true;   // far beyond what can be shown: do not even parse it
            } else {
                s = sanitizeArticle(e.data, packId, e.path);
            }
        }
        return QPair<ZimEntry, SanitizedArticle>(e, s);
    }));
}

void LibraryEngine::apply(const QString &, bool record, const QString &requested, const ZimEntry &e, const SanitizedArticle &s)
{
    QVariantMap a;
    a[u"requested"_s] = requested;
    if (!e.found) {
        a[u"kind"_s] = u"missing"_s;
        a[u"title"_s] = requested;
    } else if (e.mimeType.startsWith(u"text/html"_s)) {
        a[u"title"_s] = !s.title.isEmpty() ? s.title : e.title;
        a[u"path"_s] = e.path;
        if (s.truncated) {
            a[u"kind"_s] = u"toolarge"_s;   // never a half article: say so
        } else {
            a[u"kind"_s] = u"article"_s;
            a[u"html"_s] = s.html;
            a[u"blocked"_s] = s.blockedRemote;
        }
    } else if (e.mimeType.startsWith(u"image/"_s)) {
        a[u"kind"_s] = u"image"_s;
        a[u"title"_s] = e.title.isEmpty() ? e.path : e.title;
        a[u"imageUrl"_s] = packImageUrl(m_packId, e.path);
        a[u"path"_s] = e.path;
    } else {
        a[u"kind"_s] = u"unsupported"_s;
        a[u"title"_s] = e.title.isEmpty() ? e.path : e.title;
        a[u"mime"_s] = e.mimeType;
        a[u"sizeText"_s] = sizeText(e.data.size());
        a[u"path"_s] = e.path;
    }
    if (record && e.found) {
        m_history = m_history.mid(0, m_hpos + 1);
        m_history.append(e.path);
        m_hpos = int(m_history.size()) - 1;
    }
    m_article = a;
    Q_EMIT articleChanged();
}

void LibraryEngine::openArticle(const QString &path) { show(path, true); }
void LibraryEngine::openLink(const QString &url)
{
    if (!url.startsWith(u"zim:"_s))
        return;   // only links into the pack are ever followed
    const QString rest = url.mid(4);
    const qsizetype slash = rest.indexOf(u'/');
    if (slash <= 0)
        return;
    const QString pack = QUrl::fromPercentEncoding(rest.left(slash).toUtf8());
    if (pack != m_packId)
        return;
    show(QUrl::fromPercentEncoding(rest.mid(slash + 1).toUtf8()), true);
}
void LibraryEngine::home()
{
    if (!m_zim)
        return;
    const QString main = m_zim->info().mainPath;
    show(!main.isEmpty() ? main : m_zim->randomPath(), true);
}
void LibraryEngine::random()
{
    if (m_zim)
        show(m_zim->randomPath(), true);
}
void LibraryEngine::back()
{
    if (canBack()) { --m_hpos; show(m_history[m_hpos], false); }
}
void LibraryEngine::forward()
{
    if (canForward()) { ++m_hpos; show(m_history[m_hpos], false); }
}

// ---- map ----------------------------------------------------------------------------------------------------------
MapEngine::MapEngine(PacksEngine *packs, const QString &dataDir, QObject *parent)
    : QObject(parent), m_packs(packs), m_state(dataDir)
{
    if (!m_state.load())
        m_stateError = tr("Your saved markers could not be read, so nothing was changed. The file is %1").arg(m_state.path());
    connect(&m_indexWatch, &QFutureWatcher<void>::finished, this, &MapEngine::indexChanged);
    connect(packs, &PacksEngine::packRemoved, this, [this](const QString &id) { if (id == m_packId) close(); });
}

MapEngine::~MapEngine()
{
    m_indexWatch.waitForFinished();
}

void MapEngine::close()
{
    m_indexWatch.waitForFinished();
    m_data.reset();
    m_packId.clear();
    m_name.clear();
    m_attribution.clear();
    m_view.clear();
    Q_EMIT loadedChanged();
    Q_EMIT indexChanged();
}

bool MapEngine::openPack(const QString &packId)
{
    const PackInfo p = m_packs->manager()->pack(packId);
    if (!p.installed || p.kind != u"map"_s) {
        m_error = tr("That map pack is not installed.");
        Q_EMIT loadedChanged();
        return false;
    }
    QString err;
    std::shared_ptr<MapData> d(MapData::open(p.path, &err).release());
    if (!d) {
        m_error = tr("This map could not be opened. %1").arg(err);
        Q_EMIT loadedChanged();
        return false;
    }
    m_indexWatch.waitForFinished();
    m_data = d;
    m_packId = packId;
    const PmTilesArchive &a = d->archive();
    m_name = !p.title.isEmpty() && p.local ? (a.name().isEmpty() ? p.title : a.name()) : p.title;
    m_attribution = a.attribution();
    if (!p.attribution.isEmpty())
        m_attribution = p.attribution;
    m_error.clear();
    const PmHeader &h = a.header();
    m_view = {{u"west"_s, h.minLon}, {u"south"_s, h.minLat}, {u"east"_s, h.maxLon}, {u"north"_s, h.maxLat},
              {u"minZoom"_s, h.minZoom}, {u"maxZoom"_s, h.maxZoom}, {u"lon"_s, h.centerLon}, {u"lat"_s, h.centerLat}};
    Q_EMIT loadedChanged();
    Q_EMIT indexChanged();
    m_indexWatch.setFuture(QtConcurrent::run([d] { d->buildPlaceIndex(9); }));
    return true;
}

void MapEngine::openFirstAvailable()
{
    if (m_data)
        return;
    const QVariantList maps = m_packs->ofKind(u"map"_s, true);
    if (!maps.isEmpty())
        openPack(maps.first().toMap().value(u"id"_s).toString());
}

QVariantList MapEngine::searchPlaces(const QString &text) const
{
    QVariantList out;
    if (!m_data)
        return out;
    for (const Place &p : m_data->searchPlaces(text))
        out.append(QVariantMap{{u"name"_s, p.name}, {u"kind"_s, p.kind}, {u"lon"_s, p.lon}, {u"lat"_s, p.lat}, {u"zoom"_s, p.zoom}});
    return out;
}

QVariantList MapEngine::markers() const
{
    QVariantList out;
    for (const QJsonValue &v : m_state.markers()) {
        const QJsonObject o = v.toObject();
        out.append(QVariantMap{{u"id"_s, o.value(u"id"_s).toString()}, {u"name"_s, o.value(u"name"_s).toString()}, {u"note"_s, o.value(u"note"_s).toString()},
                               {u"lon"_s, o.value(u"lon"_s).toDouble()}, {u"lat"_s, o.value(u"lat"_s).toDouble()},
                               {u"coords"_s, formatCoords(o.value(u"lon"_s).toDouble(), o.value(u"lat"_s).toDouble())}});
    }
    return out;
}
QVariantList MapEngine::contacts() const
{
    QVariantList out;
    for (const QJsonValue &v : m_state.contacts())
        out.append(v.toObject().toVariantMap());
    return out;
}
bool MapEngine::saveState()
{
    QString err;
    const bool ok = m_state.save(&err);
    m_stateError = ok ? QString() : err;
    return ok;
}
QString MapEngine::addMarker(const QString &name, const QString &note, double lon, double lat)
{
    const QString id = m_state.addMarker(name.trimmed().isEmpty() ? tr("Marker") : name.trimmed(), note, lon, lat);
    saveState();
    Q_EMIT markersChanged();
    return id;
}
void MapEngine::removeMarker(const QString &id)
{
    if (m_state.removeMarker(id)) {
        saveState();
        Q_EMIT markersChanged();
    }
}
void MapEngine::setMe(double lon, double lat)
{
    m_state.setMe(lon, lat);
    saveState();
    Q_EMIT meChanged();
}
void MapEngine::clearMe()
{
    m_state.clearMe();
    saveState();
    Q_EMIT meChanged();
}
QString MapEngine::addContact(const QString &callsign, const QString &nodeId, const QString &note)
{
    const QString id = m_state.addContact(callsign.trimmed(), nodeId.trimmed(), note);
    saveState();
    Q_EMIT contactsChanged();
    return id;
}
void MapEngine::removeContact(const QString &id)
{
    if (m_state.removeContact(id)) {
        saveState();
        Q_EMIT contactsChanged();
    }
}
bool MapEngine::positionAvailable() const
{
#ifdef TAOS_SURVIVAL_POSITIONING
    return m_pos != nullptr;
#else
    return false;
#endif
}
void MapEngine::startPosition()
{
#ifdef TAOS_SURVIVAL_POSITIONING
    if (m_pos)
        return;
    m_pos = QGeoPositionInfoSource::createDefaultSource(this);
    if (!m_pos) {
        Q_EMIT meChanged();
        return;
    }
    connect(m_pos, &QGeoPositionInfoSource::positionUpdated, this, [this](const QGeoPositionInfo &info) {
        if (!info.isValid())
            return;
        m_devLon = info.coordinate().longitude();   // kept in memory only: a device position is never written to disk
        m_devLat = info.coordinate().latitude();
        m_hasDev = true;
        Q_EMIT meChanged();
    });
    // a service that is installed but not running (no GeoClue) reports an error: then there is no device position
    connect(m_pos, &QGeoPositionInfoSource::errorOccurred, this, [this](QGeoPositionInfoSource::Error e) {
        if (e == QGeoPositionInfoSource::NoError || !m_pos)
            return;
        m_pos->stopUpdates();
        m_pos->deleteLater();
        m_pos = nullptr;
        Q_EMIT meChanged();
    });
    m_pos->setUpdateInterval(5000);
    m_pos->startUpdates();
    Q_EMIT meChanged();
#endif
    // a device position is never saved: only a position the person set by hand is kept
}
QString MapEngine::formatCoords(double lon, double lat) const
{
    return u"%1° %2, %3° %4"_s.arg(std::abs(lat), 0, 'f', 5).arg(lat >= 0 ? u"N"_s : u"S"_s).arg(std::abs(lon), 0, 'f', 5).arg(lon >= 0 ? u"E"_s : u"W"_s);
}

// ---- radios -------------------------------------------------------------------------------------------------------
RadioEngine::RadioEngine(QObject *parent) : QObject(parent)
{
    connect(&m_watch, &QFutureWatcher<ProbeResult>::finished, this, [this] {
        const ProbeResult r = m_watch.result();
        for (RadioCandidate &c : m_list)
            if (c.device == m_probing) {
                c.probed = true;
                c.protocol = r.protocol;
                c.firmware = r.firmware;
                c.probeNote = r.note;
            }
        m_probing.clear();
        Q_EMIT changed();
    });
}
QVariantList RadioEngine::radios() const
{
    QVariantList out;
    for (const RadioCandidate &c : m_list) {
        QVariantMap m;
        m[u"device"_s] = c.device; m[u"name"_s] = c.product.isEmpty() ? c.chip : c.product; m[u"vendor"_s] = c.vendor;
        m[u"usbId"_s] = u"%1:%2"_s.arg(c.vid, 4, 16, QLatin1Char('0')).arg(c.pid, 4, 16, QLatin1Char('0'));
        m[u"chip"_s] = c.chip; m[u"boards"_s] = c.boards; m[u"confidence"_s] = c.confidence; m[u"inUse"_s] = c.inUse;
        m[u"probed"_s] = c.probed; m[u"protocol"_s] = c.protocol; m[u"firmware"_s] = c.firmware; m[u"note"_s] = c.probeNote;
        m[u"serial"_s] = c.serial; m[u"probing"_s] = c.device == m_probing;
        out.append(m);
    }
    return out;
}
int RadioEngine::confirmedCount() const
{
    int n = 0;
    for (const RadioCandidate &c : m_list)
        if (!c.protocol.isEmpty()) ++n;
    return n;
}
void RadioEngine::scan()
{
    QList<RadioCandidate> fresh = RadioDetector().scan();
    for (RadioCandidate &c : fresh)
        for (const RadioCandidate &old : std::as_const(m_list))
            if (old.device == c.device && old.vid == c.vid && old.pid == c.pid && old.serial == c.serial) {
                c.probed = old.probed; c.protocol = old.protocol; c.firmware = old.firmware; c.probeNote = old.probeNote;
            }
    m_list = fresh;
    m_scanned = true;
    Q_EMIT changed();
}
void RadioEngine::probe(const QString &device)
{
    if (!m_probing.isEmpty())
        return;   // one device at a time
    for (const RadioCandidate &c : std::as_const(m_list))
        if (c.device == device) {
            m_probing = device;
            Q_EMIT changed();
            m_watch.setFuture(QtConcurrent::run([c] { return RadioDetector().probe(c); }));
            return;
        }
}

// ---- kits ---------------------------------------------------------------------------------------------------------
KitEngine::KitEngine(PacksEngine *packs, RadioEngine *radios, MapEngine *map, QObject *parent)
    : QObject(parent), m_packs(packs), m_radios(radios), m_map(map)
{
    connect(packs, &PacksEngine::changed, this, &KitEngine::refresh);
    connect(radios, &RadioEngine::changed, this, &KitEngine::refresh);
}
void KitEngine::refresh()
{
    KitContext ctx;
    for (const PackInfo &p : m_packs->manager()->packs()) {
        if (!p.installed) continue;
        if (p.kind == u"zim"_s) ++ctx.zimPacks; else ++ctx.mapPacks;
        ctx.packBytes += p.installedBytes;
    }
    ctx.radiosPossible = m_radios->possibleCount();
    ctx.radiosConfirmed = m_radios->confirmedCount();
    const QDir ps(u"/sys/class/power_supply"_s);
    if (ps.exists()) {
        ctx.batteries = 0;
        for (const QString &n : ps.entryList(QDir::Dirs | QDir::NoDotAndDotDot)) {
            QFile f(ps.filePath(n + u"/type"_s));
            if (f.open(QIODevice::ReadOnly) && f.readAll().trimmed() == "Battery")
                ++ctx.batteries;
        }
    }
    const QStorageInfo st(m_packs->manager()->packsDir());
    ctx.freeBytes = st.isValid() ? st.bytesAvailable() : -1;
#ifdef TAOS_SURVIVAL_POSITIONING
    // a source that can be created is one that can give a position; a plugin that is only installed is not
    static const int probed = [] {   // asked once: creating a source is not free
        auto *probe = QGeoPositionInfoSource::createDefaultSource(nullptr);
        delete probe;
        return probe ? 1 : 0;
    }();
    ctx.positionSources = probed;
#endif
    ctx.arch = QSysInfo::currentCpuArchitecture();
    const QJsonDocument d = QJsonDocument::fromJson(bundledCatalog(u"kits.json"_s));
    m_kits.clear();
    for (const QJsonValue &v : evaluateKits(d.object(), ctx))
        m_kits.append(v.toObject().toVariantMap());
    Q_EMIT changed();
}

// ---- the whole -----------------------------------------------------------------------------------------------------
SurvivalBackend::SurvivalBackend(const QString &dataDir, QObject *parent)
    : QObject(parent), m_packs(dataDir), m_library(&m_packs), m_map(&m_packs, dataDir), m_radios(), m_kits(&m_packs, &m_radios, &m_map)
{
    m_radios.scan();
    m_kits.refresh();
    auto *packs = &m_packs;
    m_catalog = new CatalogEngine(packs, this);
    m_collections = new CollectionsEngine(packs, this);
    m_mapPacks = new MapPacksEngine(packs, &m_exec, this);
    m_edu = new EduEngine(dataDir, &m_exec, this);
    m_models = new ModelsEngine(this);
}

void SurvivalBackend::setMesh(MeshBackend *b)
{
    if (m_mesh)
        m_mesh->disconnect(this);
    m_mesh = b ? b : &m_fakeMesh;
    for (auto sig : {&MeshBackend::radiosChanged, &MeshBackend::identitiesChanged, &MeshBackend::messagesChanged})
        connect(m_mesh, sig, this, &SurvivalBackend::meshChanged);
    Q_EMIT meshChanged();
}
QVariantList SurvivalBackend::meshMessages(const QString &threadId) const
{
    QVariantList out;
    for (const MeshMessage &m : m_mesh->messages(threadId))
        out.append(QVariantMap{{u"id"_s, m.id}, {u"threadId"_s, m.threadId}, {u"from"_s, m.fromId}, {u"to"_s, m.toId}, {u"text"_s, m.text},
                               {u"timeMs"_s, m.timeMs}, {u"outgoing"_s, m.outgoing}, {u"delivery"_s, m.delivery}, {u"trust"_s, m.trust}});
    return out;
}
QVariantList SurvivalBackend::meshIdentities() const
{
    QVariantList out;
    for (const MeshIdentity &i : m_mesh->identities())
        out.append(QVariantMap{{u"id"_s, i.id}, {u"callsign"_s, i.callsign}, {u"owner"_s, i.owner}, {u"self"_s, i.isSelf},
                               {u"hasPosition"_s, i.hasPosition}, {u"lon"_s, i.lon}, {u"lat"_s, i.lat}, {u"lastHeardMs"_s, i.lastHeardMs}});
    return out;
}
QString SurvivalBackend::meshSend(const QString &threadId, const QString &text)
{
    if (text.trimmed().isEmpty())
        return tr("Type a message first.");
    const MeshSendResult r = m_mesh->send(threadId, text);
    return r.accepted ? QString() : (r.error.isEmpty() ? tr("The message was not accepted.") : r.error);
}

void registerSurvivalQml(SurvivalBackend *backend)
{
    qmlRegisterType<MapView>("Taos.Survival", 1, 0, "MapView");
    qmlRegisterSingletonType<SurvivalBackend>("Taos.Survival", 1, 0, "Survival", [backend](QQmlEngine *e, QJSEngine *) -> QObject * {
        e->addImageProvider(u"zimimg"_s, new ZimImageProvider);
        QQmlEngine::setObjectOwnership(backend, QQmlEngine::CppOwnership);
        return backend;
    });
}

void registerSurvivalQmlLazy(SurvivalBackend *(*make)())
{
    qmlRegisterType<MapView>("Taos.Survival", 1, 0, "MapView");
    qmlRegisterSingletonType<SurvivalBackend>("Taos.Survival", 1, 0, "Survival", [make](QQmlEngine *e, QJSEngine *) -> QObject * {
        e->addImageProvider(u"zimimg"_s, new ZimImageProvider);
        SurvivalBackend *b = make();
        QQmlEngine::setObjectOwnership(b, QQmlEngine::CppOwnership);
        return b;
    });
}

} // namespace Taos::Survival
