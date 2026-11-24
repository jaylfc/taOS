#pragma once
// The Store's catalogue logic, with no network and no UI: normalising the controller's catalogue entries
// (`GET /api/store/catalog`, tinyagentos/routes/store.py:224) and the built-in web app list, merging them,
// searching, sectioning, and deciding what an entry needs (Docker, a licence, hardware fit).
#include <QJsonArray>
#include <QJsonObject>
#include <QSet>
#include <QHash>
#include <QVariantList>
#include <QVariantMap>

namespace Taos::Store {

/// Section ids, in the order of the sidebar.
QStringList sectionIds();

/// One controller entry as the Store shows it. Unknown fields are ignored; missing ones get defaults.
/// Keys: id name kind section category type version description tagline icon letter installed state
/// updateAvailable upstreamVersion installMethod runtime needsDocker runtimeText ramMb diskMb sizeText license
/// weightsLicense licenseClass repo stars variants fit fitText url host webapp
QVariantMap normalizeServerEntry(const QJsonObject &o);

/// An entry of catalog/webapps.json (or a server entry of type webapp).
QVariantMap normalizeWebappEntry(const QJsonObject &o);

/// "1.2 GB", "350 MB", "" for 0.
QString sizeText(double mb);

/// kind of a controller `type`: app, agent, model, plugin, mcp, webapp.
QString kindOfType(const QString &type);
QString sectionOfKind(const QString &kind);

/// Hardware fit of `hardware_tiers` on `profileId`, as the server's _build_app_items decides
/// (store.py:172): "green" compatible, "yellow" degraded, "red" unsupported, "" unknown.
QString fitFromTiers(const QJsonObject &tiers, const QString &profileId);
QString fitText(const QString &fit);

class Catalog {
public:
    void setServer(const QJsonArray &entries);
    void setBuiltinWebapps(const QJsonArray &apps);
    void setProfile(const QString &profileId);
    /// `/api/models` rows: sets `fit` of model entries from the controller's own compatibility.
    void setModelFit(const QJsonArray &models);
    void markInstalled(const QString &id, bool installed);
    void setInstalled(const QSet<QString> &ids);
    void setInstanceCounts(const QHash<QString, int> &counts);

    bool empty() const { return m_entries.isEmpty(); }
    int size() const { return m_entries.size(); }
    QVariantList all() const;
    QVariantMap entry(const QString &id) const;
    bool has(const QString &id) const { return m_index.contains(id); }

    /// Entries of a section ("apps", "webapps", "models", "plugins", "installed", "updates"), filtered by a
    /// search text and a chip (a category id, "" or "all" for every one). Best match first when searching,
    /// else by name.
    QVariantList section(const QString &section, const QString &query = {}, const QString &chip = {}) const;
    /// Category chips of a section: [{id,label,count}], largest first, "all" first.
    QVariantList chips(const QString &section) const;
    /// Search across every kind.
    QVariantList search(const QString &query) const;
    /// Discover: [{id,title,items}] groups.
    QVariantList discover() const;
    int count(const QString &section) const;

    static int score(const QVariantMap &e, const QStringList &words);

private:
    void rebuild();
    QJsonArray m_serverArr, m_webArr;
    QList<QVariantMap> m_entries;
    QHash<QString, int> m_index;
    QString m_profile;
    QHash<QString, QString> m_modelFit;
    QSet<QString> m_installed;          ///< ids the controller lists as installed (installed-v2), added to the flags
    QSet<QString> m_forceInstalled, m_forceRemoved;   ///< what this window just did, until the next refresh
    QHash<QString, int> m_instances;    ///< web app store id -> number of instances
};

} // namespace Taos::Store
