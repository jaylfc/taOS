// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The faces' view of the engines: QObjects with properties and invokable methods for QML. All logic lives in engine/;
// these classes only translate to and from QML values and keep what the window shows.
#include <QFutureWatcher>
#include <QHash>
#include <QObject>
#include <QQuickImageProvider>
#include <QReadWriteLock>
#include <QTimer>
#include <QVariant>
#include <memory>

#include "mapdata.h"
#include "meshinterface.h"
#include "packmanager.h"
#include "radiodetector.h"
#include "statestore.h"
#include "socketmeshbackend.h"
#include "takengine.h"
#include "articlehtml.h"
#include "zimarchive.h"

#ifdef TAOS_SURVIVAL_POSITIONING
class QGeoPositionInfoSource;
#endif

namespace Taos::Survival {

/// Open archives by pack id, shared with the image provider (which runs on other threads).
class ArchiveRegistry
{
public:
    static ArchiveRegistry &instance();
    void set(const QString &id, std::shared_ptr<ZimArchive> a);
    std::shared_ptr<ZimArchive> get(const QString &id) const;
    void remove(const QString &id);

private:
    mutable QReadWriteLock m_lock;
    QHash<QString, std::shared_ptr<ZimArchive>> m_map;
};

/// image://zimimg/<pack id>/<path in the pack>: pictures from the open packs only, scaled down to the page width.
class ZimImageProvider : public QQuickImageProvider
{
public:
    ZimImageProvider() : QQuickImageProvider(QQuickImageProvider::Image) {}
    QImage requestImage(const QString &id, QSize *size, const QSize &requestedSize) override;
};

QString sizeText(qint64 bytes);

class PacksEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList packs READ packs NOTIFY changed)
    Q_PROPERTY(QString message READ message NOTIFY messageChanged)
    Q_PROPERTY(QString dir READ dir CONSTANT)
    Q_PROPERTY(QString freeText READ freeText NOTIFY changed)
public:
    explicit PacksEngine(const QString &dataDir, QObject *parent = nullptr);
    PackManager *manager() { return &m_pm; }
    QVariantList packs() const;
    QString message() const { return m_message; }
    QString dir() const { return m_pm.packsDir(); }
    QString freeText() const;
    Q_INVOKABLE QVariantList ofKind(const QString &kind, bool installedOnly = false) const;
    Q_INVOKABLE QVariantMap pack(const QString &id) const;
    Q_INVOKABLE bool download(const QString &id);
    Q_INVOKABLE void cancel(const QString &id) { m_pm.cancel(id); }
    Q_INVOKABLE bool remove(const QString &id);
    Q_INVOKABLE QString verify(const QString &id);
    Q_INVOKABLE bool importFile(const QString &path, const QString &sha256 = QString());
    Q_INVOKABLE void acceptLicence(const QString &id) { m_pm.acceptLicence(id); }
    Q_INVOKABLE void rescan() { m_pm.rescan(); }
Q_SIGNALS:
    void changed();
    void messageChanged();
    void packRemoved(const QString &id);
    void packInstalled(const QString &id);
private:
    QVariantMap toMap(const PackInfo &p) const;
    void setMessage(const QString &m) { m_message = m; Q_EMIT messageChanged(); }
    PackManager m_pm;
    QString m_message;
    QHash<QString, QPair<qint64, qint64>> m_progress;
};

class LibraryEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString packId READ packId NOTIFY packChanged)
    Q_PROPERTY(QVariantMap info READ info NOTIFY packChanged)
    Q_PROPERTY(QVariantList results READ results NOTIFY resultsChanged)
    Q_PROPERTY(QString query READ query NOTIFY resultsChanged)
    Q_PROPERTY(QVariantMap article READ article NOTIFY articleChanged)
    Q_PROPERTY(bool canBack READ canBack NOTIFY articleChanged)
    Q_PROPERTY(bool canForward READ canForward NOTIFY articleChanged)
    Q_PROPERTY(QString error READ error NOTIFY packChanged)
public:
    explicit LibraryEngine(PacksEngine *packs, QObject *parent = nullptr);
    QString packId() const { return m_packId; }
    QVariantMap info() const { return m_info; }
    QVariantList results() const { return m_results; }
    QString query() const { return m_query; }
    QVariantMap article() const { return m_article; }
    bool canBack() const { return m_hpos > 0; }
    bool canForward() const { return m_hpos + 1 < m_history.size(); }
    QString error() const { return m_error; }
    Q_INVOKABLE bool open(const QString &packId);
    Q_INVOKABLE void close();
    Q_INVOKABLE void search(const QString &text);
    Q_INVOKABLE void openArticle(const QString &path);
    Q_INVOKABLE void openLink(const QString &url);       // zim:<pack>/<path>
    Q_INVOKABLE void home();
    Q_INVOKABLE void random();
    Q_INVOKABLE void back();
    Q_INVOKABLE void forward();
Q_SIGNALS:
    void packChanged();
    void resultsChanged();
    void articleChanged();
private:
    void show(const QString &path, bool record);
    void apply(const QString &path, bool record, const QString &requested, const ZimEntry &e, const SanitizedArticle &s);
    PacksEngine *m_packs;
    std::shared_ptr<ZimArchive> m_zim;
    QString m_packId, m_query, m_error;
    QVariantMap m_info, m_article;
    QVariantList m_results;
    QStringList m_history;
    int m_hpos = -1;
    quint64 m_request = 0;   // only the newest article request may show its result
    QFutureWatcher<void> m_pending;
};

class MapEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool loaded READ loaded NOTIFY loadedChanged)
    Q_PROPERTY(QString packId READ packId NOTIFY loadedChanged)
    Q_PROPERTY(QString name READ name NOTIFY loadedChanged)
    Q_PROPERTY(QString attribution READ attribution NOTIFY loadedChanged)
    Q_PROPERTY(QString error READ error NOTIFY loadedChanged)
    Q_PROPERTY(QVariantMap view READ view NOTIFY loadedChanged)   // {lon, lat, zoom, minZoom, maxZoom, west, south, east, north}
    Q_PROPERTY(bool indexReady READ indexReady NOTIFY indexChanged)
    Q_PROPERTY(int placeCount READ placeCount NOTIFY indexChanged)
    Q_PROPERTY(QVariantList markers READ markers NOTIFY markersChanged)
    Q_PROPERTY(QVariantList contacts READ contacts NOTIFY contactsChanged)
    Q_PROPERTY(bool hasMe READ hasMe NOTIFY meChanged)
    Q_PROPERTY(double meLon READ meLon NOTIFY meChanged)
    Q_PROPERTY(double meLat READ meLat NOTIFY meChanged)
    Q_PROPERTY(QString meSource READ meSource NOTIFY meChanged)   // device | manual | none
    Q_PROPERTY(bool positionAvailable READ positionAvailable NOTIFY meChanged)
    Q_PROPERTY(QString stateError READ stateError NOTIFY markersChanged)
public:
    explicit MapEngine(PacksEngine *packs, const QString &dataDir, QObject *parent = nullptr);
    ~MapEngine() override;
    MapData *data() const { return m_data.get(); }
    std::shared_ptr<MapData> dataShared() const { return m_data; }
    bool loaded() const { return bool(m_data); }
    QString packId() const { return m_packId; }
    QString name() const { return m_name; }
    QString attribution() const { return m_attribution; }
    QString error() const { return m_error; }
    QVariantMap view() const { return m_view; }
    bool indexReady() const { return m_data && m_data->placeIndexReady(); }
    int placeCount() const { return m_data ? m_data->placeCount() : 0; }
    QVariantList markers() const;
    QVariantList contacts() const;
    bool hasMe() const { return m_state.hasMe() || m_hasDev; }
    double meLon() const { return m_state.hasMe() ? m_state.meLon() : m_devLon; }
    double meLat() const { return m_state.hasMe() ? m_state.meLat() : m_devLat; }
    QString meSource() const { return m_state.hasMe() ? QStringLiteral("manual") : m_hasDev ? QStringLiteral("device") : QStringLiteral("none"); }
    bool positionAvailable() const;
    QString stateError() const { return m_stateError; }
    Q_INVOKABLE bool openPack(const QString &packId);
    Q_INVOKABLE void openFirstAvailable();
    Q_INVOKABLE void close();
    Q_INVOKABLE QVariantList searchPlaces(const QString &text) const;
    Q_INVOKABLE QString addMarker(const QString &name, const QString &note, double lon, double lat);
    Q_INVOKABLE void removeMarker(const QString &id);
    Q_INVOKABLE void setMe(double lon, double lat);
    Q_INVOKABLE void clearMe();
    Q_INVOKABLE QString addContact(const QString &callsign, const QString &nodeId, const QString &note);
    Q_INVOKABLE void removeContact(const QString &id);
    Q_INVOKABLE void startPosition();
    Q_INVOKABLE QString formatCoords(double lon, double lat) const;
Q_SIGNALS:
    void loadedChanged();
    void indexChanged();
    void markersChanged();
    void contactsChanged();
    void meChanged();
private:
    bool saveState();
    PacksEngine *m_packs;
    std::shared_ptr<MapData> m_data;
    QString m_packId, m_name, m_attribution, m_error, m_stateError;
    bool m_hasDev = false;
    double m_devLon = 0, m_devLat = 0;
    QVariantMap m_view;
    StateStore m_state;
    QFutureWatcher<void> m_indexWatch;
#ifdef TAOS_SURVIVAL_POSITIONING
    QGeoPositionInfoSource *m_pos = nullptr;
#endif
};

class RadioEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList radios READ radios NOTIFY changed)
    Q_PROPERTY(QString probing READ probing NOTIFY changed)
    Q_PROPERTY(bool scanned READ scanned NOTIFY changed)
public:
    explicit RadioEngine(QObject *parent = nullptr);
    QVariantList radios() const;
    QString probing() const { return m_probing; }
    bool scanned() const { return m_scanned; }
    int confirmedCount() const;
    int possibleCount() const { return int(m_list.size()); }
    Q_INVOKABLE void scan();
    Q_INVOKABLE void probe(const QString &device);
Q_SIGNALS:
    void changed();
private:
    QList<RadioCandidate> m_list;
    QString m_probing;
    bool m_scanned = false;
    QFutureWatcher<ProbeResult> m_watch;
};

class KitEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList kits READ kits NOTIFY changed)
public:
    KitEngine(PacksEngine *packs, RadioEngine *radios, MapEngine *map, QObject *parent = nullptr);
    QVariantList kits() const { return m_kits; }
    Q_INVOKABLE void refresh();
Q_SIGNALS:
    void changed();
private:
    PacksEngine *m_packs;
    RadioEngine *m_radios;
    MapEngine *m_map;
    QVariantList m_kits;
};

class SurvivalBackend : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QObject *packs READ packs CONSTANT)
    Q_PROPERTY(QObject *library READ library CONSTANT)
    Q_PROPERTY(QObject *map READ map CONSTANT)
    Q_PROPERTY(QObject *radios READ radios CONSTANT)
    Q_PROPERTY(QObject *kits READ kits CONSTANT)
    Q_PROPERTY(QObject *tak READ tak CONSTANT)
    Q_PROPERTY(QObject *firmware READ firmware CONSTANT)
    Q_PROPERTY(QObject *switcher READ switcher CONSTANT)
    Q_PROPERTY(QObject *mesh READ mesh NOTIFY meshChanged)
    Q_PROPERTY(bool meshAvailable READ meshAvailable NOTIFY meshChanged)
    Q_PROPERTY(QString meshReason READ meshReason NOTIFY meshChanged)
public:
    explicit SurvivalBackend(const QString &dataDir, QObject *parent = nullptr);
    QObject *packs() { return &m_packs; }
    QObject *library() { return &m_library; }
    QObject *map() { return &m_map; }
    QObject *radios() { return &m_radios; }
    QObject *kits() { return &m_kits; }
    QObject *tak() { return &m_tak; }
    QObject *firmware() { return &m_firmware; }
    QObject *switcher() { return &m_switch; }
    SwitchEngine *switchEngine() { return &m_switch; }
    TakEngine *takEngine() { return &m_tak; }
    FirmwareEngine *firmwareEngine() { return &m_firmware; }
    QObject *mesh() { return m_mesh; }
    bool meshAvailable() const { return m_mesh && m_mesh->available(); }
    QString meshReason() const { return m_mesh ? m_mesh->unavailableReason() : QString(); }
    /// Replaces the stand-in with the real mesh engine's backend (not owned unless it has this object as parent).
    void setMesh(MeshBackend *b);
    /// Messages of a thread (all when empty), as QML values; empty while the engine is missing.
    Q_INVOKABLE QVariantList meshMessages(const QString &threadId = QString()) const;
    Q_INVOKABLE QVariantList meshIdentities() const;
    /// Returns an error in words, or an empty string when the message was queued.
    Q_INVOKABLE QString meshSend(const QString &threadId, const QString &text);
    PacksEngine *packsEngine() { return &m_packs; }
    MapEngine *mapEngine() { return &m_map; }
Q_SIGNALS:
    void meshChanged();
private:
    PacksEngine m_packs;
    LibraryEngine m_library;
    MapEngine m_map;
    RadioEngine m_radios;
    KitEngine m_kits;
    FakeMeshBackend m_fakeMesh;
    MeshClient m_meshClient;
    SocketMeshBackend m_socketMesh;
    MeshBackend *m_mesh = &m_fakeMesh;
    TakEngine m_tak;
    FirmwareClient m_fwClient;
    FirmwareEngine m_firmware;
    SwitchEngine m_switch;
    QTimer m_meshWatch;
};

/// Registers the Taos.Survival QML module: the Survival singleton (which also adds the zimimg image provider to the
/// engine that first asks for it) and MapView. The backend must outlive every engine that uses it.
void registerSurvivalQml(SurvivalBackend *backend);
/// The same, with the backend made when the first engine asks for it (after the application object exists).
void registerSurvivalQmlLazy(SurvivalBackend *(*make)());

} // namespace Taos::Survival
