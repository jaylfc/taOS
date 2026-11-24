#pragma once
// Settings > Apps: every app on this device in one list. Native apps (their launcher entries), container apps (the
// controller's installed list, read only here) and web app instances (the web apps engine: open, rename, clone,
// clear data, remove). A face over the same engines the Store uses.
#include <QObject>
#include <QPointer>
#include <QVariantList>
#include <functional>

#include "taosqt/client.h"
#include "webappsservice.h"

namespace Taos::Store {

/// One launcher entry (the keys of [Desktop Entry] this app reads).
struct DesktopEntry {
    QString file, name, comment, icon, exec, appId, webApp;
    bool noDisplay = false;
    static DesktopEntry parse(const QString &path);
    /// Exec with the field codes (%U, %f ...) removed, split into a program and its arguments.
    QStringList command() const;
};

class AppsOverview : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList native READ native NOTIFY changed)
    Q_PROPERTY(QVariantList containers READ containers NOTIFY changed)
    Q_PROPERTY(QVariantList webInstances READ webInstances NOTIFY changed)
    Q_PROPERTY(bool containersLoaded READ containersLoaded NOTIFY changed)
    Q_PROPERTY(QString containersError READ containersError NOTIFY changed)
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
    Q_PROPERTY(bool browserAvailable READ browserAvailable NOTIFY changed)
    Q_PROPERTY(QString browserText READ browserText NOTIFY changed)
    Q_PROPERTY(int revision READ revision NOTIFY changed)
public:
    explicit AppsOverview(QObject *parent = nullptr);
    /// Tests: folders to scan instead of $XDG_DATA_DIRS, a launcher that records instead of starting, a private
    /// web apps engine, a client.
    void setDesktopDirs(const QStringList &dirs) { m_dirs = dirs; rescan(); }
    void setLauncher(std::function<bool(const QString &, const QStringList &)> l) { m_launcher = std::move(l); }
    void setWebappOptions(const TaosWebapp::Options &o);
    void setClientForTest(Taos::Client *c) { m_client = c; }

    QVariantList native() const { return m_native; }
    QVariantList containers() const { return m_containers; }
    QVariantList webInstances() const { return m_web->instances(); }
    bool containersLoaded() const { return m_containersLoaded; }
    QString containersError() const { return m_containersError; }
    bool active() const { return m_active; }
    void setActive(bool on);
    bool browserAvailable() const { return m_web->browserAvailable(); }
    QString browserText() const { return m_web->browserText(); }
    int revision() const { return m_revision + m_web->revision(); }
    WebAppsService *web() { return m_web; }
    Q_INVOKABLE QObject *webapps() { return m_web; }

    Q_INVOKABLE void refresh();
    Q_INVOKABLE void rescan();
    /// Start a native app by its app id. False when it has no launcher entry or could not start.
    Q_INVOKABLE bool openNative(const QString &appId);
    /// Open the Store at an app (container apps are removed there).
    Q_INVOKABLE bool openInStore(const QString &appId);
    /// Open the Store at a section ("webapps", "installed" ...).
    Q_INVOKABLE bool openStore(const QString &section);

Q_SIGNALS:
    void changed();
    void activeChanged();

private:
    Taos::Client *client() const;
    QStringList defaultDirs() const;

    WebAppsService *m_web;
    QPointer<Taos::Client> m_client;
    QStringList m_dirs;
    QVariantList m_native, m_containers;
    QHash<QString, DesktopEntry> m_entries;
    std::function<bool(const QString &, const QStringList &)> m_launcher;
    QString m_containersError;
    bool m_containersLoaded = false, m_active = false, m_hooked = false;
    int m_revision = 0;
};

} // namespace Taos::Store
