// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The Store's hook for "container desktop app" entries (kind containerapp): the catalogue comes from
// `taos-appgroups catalog --json` (bundled with the engine, so it works signed out), and install, remove and open go
// through the `taos-appgroups` command. Nothing here knows how a container is made. The presentation (QML) only calls
// StoreEngine::install / remove / open with the entry's id, as for every other kind.
#include <QJsonArray>
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

    /// target: "group:<name>" (default "group:default"). acceptedHostWarning is carried for the host target.
    void install(const QString &id, const QString &target = {}, bool acceptedHostWarning = false);
    void remove(const QString &id, bool purgeData = false);
    bool open(const QString &id, const QStringList &args = {});

Q_SIGNALS:
    /// A progress line of install or remove: step n of m and its words.
    void progress(const QString &id, int n, int of, const QString &text);
    /// The command ended: ok, and the sentence to show (the engine's own words when it failed).
    void finished(const QString &id, bool ok, const QString &text);
};

} // namespace Taos::Store
