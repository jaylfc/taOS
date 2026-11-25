// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The Store's hook for "container desktop app" entries (kind containerapp): the catalogue comes from
// `taos-appgroups catalog --json` (bundled with the engine, so it works signed out), and install, remove and open go
// through the `taos-appgroups` command. Nothing here knows how a container is made. The presentation (QML) only calls
// StoreEngine::install / remove / open with the entry's id, as for every other kind.
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QSet>
#include <QString>

namespace Taos::Store {

class ContainerAppsService : public QObject {
    Q_OBJECT
public:
    explicit ContainerAppsService(QObject *parent = nullptr);
    /// The command to run: $TAOS_APPGROUPS_BIN, else the sibling of this program, else `taos-appgroups` on PATH.
    static QString program();

    /// Catalogue entries (already shaped like the Store's own); empty when the engine is not installed.
    QJsonArray catalog() const;
    QSet<QString> installedIds() const;
    /// Installed apps by id: {group, installed_version, licence_accepted}. Empty when the engine is not installed.
    QHash<QString, QJsonObject> installedInfo() const;
    /// Your app groups: [{name, display_name, state, apps}]
    QJsonArray groups() const;
    /// Creates a group (its own container): slow, so it runs in the background and ends with groupCreated.
    void createGroup(const QString &name, const QString &displayName);
    /// Moves an installed app and its data to another group; ends with finished(id, ...).
    void move(const QString &id, const QString &group);

    /// target: "group:<name>" (default "group:default"). acceptedHostWarning is carried for the host target.
    /// licenceAccepted: the person agreed to the licence the Store showed (recorded by the engine with the time and who).
    void install(const QString &id, const QString &target = {}, bool acceptedHostWarning = false, bool licenceAccepted = false);
    void remove(const QString &id, bool purgeData = false);
    /// Installs the newer version of an app (never while it runs; the engine refuses).
    void update(const QString &id);
    bool open(const QString &id, const QStringList &args = {});

Q_SIGNALS:
    /// A group was created (ok) or not (the engine's own sentence).
    void groupCreated(const QString &name, bool ok, const QString &text);
    /// A progress line of install or remove: step n of m and its words.
    void progress(const QString &id, int n, int of, const QString &text);
    /// The command ended: ok, and the sentence to show (the engine's own words when it failed).
    void finished(const QString &id, bool ok, const QString &text);
};

} // namespace Taos::Store
