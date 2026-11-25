// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// QML faces for reaching every pack source: the Kiwix catalogue (browse and search), the curated collections, maps by
// region, Kolibri education channels and the model list. All logic is in engine/; these classes translate for QML.
#include <QFutureWatcher>
#include <QObject>
#include <QVariant>

#include "backend.h"
#include "containerexec.h"
#include "education.h"
#include "kiwixcatalog.h"
#include "mapextract.h"

namespace Taos::Survival {

class CatalogEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList results READ results NOTIFY resultsChanged)
    Q_PROPERTY(int total READ total NOTIFY resultsChanged)
    Q_PROPERTY(int start READ start NOTIFY resultsChanged)
    Q_PROPERTY(bool loading READ loading NOTIFY resultsChanged)
    Q_PROPERTY(QString error READ error NOTIFY resultsChanged)
    Q_PROPERTY(QVariantList categories READ categories NOTIFY facetsChanged)
    Q_PROPERTY(QVariantList languages READ languages NOTIFY facetsChanged)
    Q_PROPERTY(bool checkingUpdates READ checkingUpdates NOTIFY updatesChanged)
    Q_PROPERTY(QString updateNote READ updateNote NOTIFY updatesChanged)
public:
    CatalogEngine(PacksEngine *packs, QObject *parent = nullptr);
    QVariantList results() const;
    int total() const { return m_total; }
    int start() const { return m_start; }
    bool loading() const { return m_loading; }
    QString error() const { return m_error; }
    QVariantList categories() const { return m_cats; }
    QVariantList languages() const { return m_langs; }
    bool checkingUpdates() const { return m_checking; }
    QString updateNote() const { return m_updateNote; }
    KiwixCatalog *catalog() { return &m_catalog; }
    Q_INVOKABLE void loadFacets();
    Q_INVOKABLE void search(const QString &query, const QString &language, const QString &category, int start = 0);
    /// Makes the entry a pack and starts its download. Returns an error in words, or empty.
    Q_INVOKABLE QString install(const QString &packId);
    Q_INVOKABLE void checkUpdates();
Q_SIGNALS:
    void resultsChanged();
    void facetsChanged();
    void updatesChanged();
private:
    void nextUpdateQuery();
    PacksEngine *m_packs;
    KiwixCatalog m_catalog, m_updates;
    QList<CatalogEntry> m_entries;
    QVariantList m_cats, m_langs;
    int m_total = 0, m_start = 0, m_id = 0;
    bool m_loading = false, m_checking = false;
    QString m_error, m_updateNote, m_q, m_lang, m_cat;
    QStringList m_pendingNames;
    QList<PackInfo> m_found;
    int m_updateId = 0;
};

class CollectionsEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList categories READ categories CONSTANT)
public:
    CollectionsEngine(PacksEngine *packs, QObject *parent = nullptr);
    QVariantList categories() const { return m_view; }
    /// The tier with everything it includes: [{id, title, sizeText, licence, restrictive, state, ...}] and totals.
    Q_INVOKABLE QVariantMap tier(const QString &slug) const;
    /// Accepts the restrictive licences of the tier (the person ticked the box) and queues every pack not yet installed.
    Q_INVOKABLE QString installTier(const QString &slug, bool acceptRestrictive);
private:
    QList<PackInfo> resolve(const QString &slug) const;
    PacksEngine *m_packs;
    QVariantList m_view;
    QHash<QString, QList<PackInfo>> m_own;       // slug -> its own resources
    QHash<QString, QString> m_includes;
};

class MapPacksEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList regions READ regions CONSTANT)
    Q_PROPERTY(QVariantList ready READ ready CONSTANT)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    Q_PROPERTY(QString status READ status NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(qint64 estimateBytes READ estimateBytes NOTIFY changed)
    Q_PROPERTY(QString estimateText READ estimateText NOTIFY changed)
public:
    MapPacksEngine(PacksEngine *packs, ContainerExec *exec, QObject *parent = nullptr);
    QVariantList regions() const { return m_regions; }
    QVariantList ready() const { return m_ready; }
    bool busy() const { return m_busy; }
    QString status() const { return m_status; }
    QString error() const { return m_error; }
    qint64 estimateBytes() const { return m_estimate; }
    QString estimateText() const { return sizeText(m_estimate); }
    /// Measures the extract (a dry run: nothing is written) for the region id.
    Q_INVOKABLE void estimate(const QString &regionId, int maxZoom);
    Q_INVOKABLE void extract(const QString &regionId, int maxZoom);
    Q_INVOKABLE void cancel();
    Q_INVOKABLE QString installReady(const QString &regionId);
Q_SIGNALS:
    void changed();
private:
    void withBuild(std::function<void(const QString &)> then);
    void run(const QString &regionId, int maxZoom, bool dry);
    PacksEngine *m_packs;
    MapExtractor m_extractor;
    QNetworkAccessManager m_nam;
    QList<MapRegion> m_all;
    QVariantList m_regions, m_ready;
    bool m_busy = false;
    QString m_status, m_error, m_outId, m_outTitle;
    qint64 m_estimate = 0;
    QString m_pendingFile;
    int m_pendingZoom = 0;
    QString m_pendingBuild;
};

class EduEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList channels READ channels NOTIFY changed)
    Q_PROPERTY(bool loading READ loading NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(QString importing READ importing NOTIFY changed)
    Q_PROPERTY(QString message READ message NOTIFY changed)
public:
    EduEngine(const QString &dataDir, ContainerExec *exec, QObject *parent = nullptr);
    QVariantList channels() const;
    bool loading() const { return m_loading; }
    QString error() const { return m_error; }
    QString importing() const { return m_importing; }
    QString message() const { return m_message; }
    Q_INVOKABLE void load();
    Q_INVOKABLE QString install(const QString &channelId, bool accepted);
Q_SIGNALS:
    void changed();
private:
    void saveInstalled();
    QString m_dataDir;
    QNetworkAccessManager m_nam;
    ChannelImporter m_importer;
    QList<EduChannel> m_channels;
    QStringList m_installed;
    bool m_loading = false;
    QString m_error, m_importing, m_message;
};

class ModelsEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList models READ models CONSTANT)
    Q_PROPERTY(QString note READ note CONSTANT)
public:
    explicit ModelsEngine(QObject *parent = nullptr);
    QVariantList models() const { return m_models; }
    QString note() const { return m_note; }
    /// Opens the taOS Store at its models section (the model catalogue lives there, not here).
    Q_INVOKABLE bool openStore();
private:
    QVariantList m_models;
    QString m_note;
};

} // namespace Taos::Survival
