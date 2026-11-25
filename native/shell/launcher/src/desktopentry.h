// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QList>
#include <QString>
#include <QStringList>

namespace taos::search {

struct DesktopAction {
    QString id, name, exec;
};

// A parsed freedesktop .desktop file (Desktop Entry Specification 1.5), the fields a launcher needs.
struct DesktopEntry {
    QString id;            // desktop file id: "org.gnome.Foo.desktop", "kde4-bar.desktop"
    QString path;
    QString name, genericName, comment, icon, exec, tryExec, path_;
    QStringList keywords, categories, onlyShowIn, notShowIn;
    QList<DesktopAction> actions;
    bool terminal = false;
    bool noDisplay = false;
    bool hidden = false;
    bool isApplication = false;
    QString taosAppId, appGroup, containerApp, webApp;   // X-taOS-AppId, X-taOS-AppGroup, X-taOS-ContainerApp, X-taOS-WebApp
    bool valid() const { return isApplication && !name.isEmpty() && !exec.isEmpty(); }
    // Should a launcher list this entry for a session running `desktops` (XDG_CURRENT_DESKTOP split)?
    bool showable(const QStringList &desktops) const;
};

// Parse the text of a desktop file. `locale` like "de_DE.UTF-8" or "de" picks Name[de_DE], Name[de].
DesktopEntry parseDesktopEntry(const QString &text, const QString &id, const QString &path, const QString &locale);

// Split a Exec value into argv: quoting per the spec, %% -> %, field codes dropped, %i -> --icon X, %c -> name.
QStringList execToArgv(const QString &exec, const DesktopEntry &e);

// Desktop file id for `file` found under the applications dir `root`: relative path, '/' -> '-'.
QString desktopIdFor(const QString &root, const QString &file);

// Every entry under `dirs` (first directory wins on equal ids), valid and showable only.
QList<DesktopEntry> scanDesktopEntries(const QStringList &dirs, const QString &locale, const QStringList &desktops);

// XDG data dirs' applications folders, user dir first.
QStringList applicationDirs();

}  // namespace taos::search
