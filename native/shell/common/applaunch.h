// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QProcessEnvironment>
#include <QString>
#include <QStringList>

namespace taos {

// The one way a shell component starts a person's application (dock, launcher, taskbar, menus).
//
// A shell component runs with an environment of its own: the layer-shell integration it asked Qt for
// (QT_WAYLAND_SHELL_INTEGRATION), the names the supervisor gave it (TAOS_SHELL_*), what the component itself exported.
// An application that inherits those misbehaves: a Qt app told to use layer-shell becomes a layer surface above every
// window, outside sway's tree. So an app gets a clean environment: everything the session gave the person (WAYLAND_DISPLAY,
// XDG_*, DBUS, locale, theme, and the Qt library and plugin paths the interim Qt prefix needs) and nothing that only
// the shell needs. It also starts in its own systemd user scope, so it is not a child of the component and gets its
// own cgroup (a dock restart never takes an app with it).
QProcessEnvironment cleanAppEnvironment(const QProcessEnvironment &in);

struct LaunchOptions {
    QString workingDirectory;           // default: the person's home
    bool useScope = true;               // systemd-run --user --scope when systemd-run and a user manager exist
    const QProcessEnvironment *base = nullptr;   // the environment to clean; default: this process's
};
bool launchApp(const QString &program, const QStringList &args, QString *error = nullptr, const LaunchOptions &o = {});

}  // namespace taos
