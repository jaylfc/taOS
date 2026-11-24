// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QFileSystemWatcher>
#include <QHash>
#include <QObject>
#include <QString>
#include <QTimer>

// ~/.config/taos/desktop.json, written by the window engine: {"default": "desktop"|"tiling",
// "workspaces": {"<name>": "desktop"|"tiling"}}. The dock only reads it.
struct DesktopModes {
    QString defaultMode = QStringLiteral("desktop");
    QHash<QString, QString> workspaces;
    QString modeFor(const QString &workspace) const;
    bool tiling(const QString &workspace) const { return modeFor(workspace) == QLatin1String("tiling"); }
    bool operator==(const DesktopModes &o) const { return defaultMode == o.defaultMode && workspaces == o.workspaces; }
    bool operator!=(const DesktopModes &o) const { return !(*this == o); }
};

namespace DesktopModeFile {
QString path();   // $TAOS_DESKTOP_MODE, else $XDG_CONFIG_HOME/taos/desktop.json, else ~/.config/taos/desktop.json
bool read(const QString &path, DesktopModes *out);   // bad values become "desktop"; false when missing or broken
}

// Watches the file (and its folder, atomic renames replace the file) like dock.json.
class DesktopModeWatcher : public QObject {
    Q_OBJECT
public:
    explicit DesktopModeWatcher(const QString &path = QString(), bool watch = true, QObject *parent = nullptr);
    const DesktopModes &modes() const { return m_m; }
    void reload();
signals:
    void changed();
private:
    void rewatch();
    QString m_path;
    DesktopModes m_m;
    QFileSystemWatcher m_watch;
    QTimer m_debounce;
    bool m_watching;
};
