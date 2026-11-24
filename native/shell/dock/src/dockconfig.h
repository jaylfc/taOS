// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QFileSystemWatcher>
#include <QObject>
#include <QStringList>
#include <QTimer>

// The per person dock setting: ~/.config/taos/dock.json
//   {"position":"bottom|left|right","attached":false,"autohide":false,"size":"small|medium|large",
//    "magnify":false,"hideInTiling":true,"pinned":["my.taos.dashboard.desktop-id", ...],"outputs":"primary|all"}
// `pinned` absent means "the defaults for this machine" (the dock computes them from the installed
// entries); once the person pins or unpins something the list is written and wins.
struct DockConfig {
    QString position = QStringLiteral("bottom");
    bool attached = false;
    bool autohide = false;
    QString size = QStringLiteral("medium");
    bool magnify = false;
    bool hideInTiling = true;   // a tiling workspace auto-hides the dock (desktop.json decides which are tiling)
    QStringList pinned;
    bool pinnedSet = false;
    QString outputs = QStringLiteral("primary");
    bool operator==(const DockConfig &o) const {
        return position == o.position && attached == o.attached && autohide == o.autohide && size == o.size
            && magnify == o.magnify && hideInTiling == o.hideInTiling && pinned == o.pinned && pinnedSet == o.pinnedSet && outputs == o.outputs;
    }
    bool operator!=(const DockConfig &o) const { return !(*this == o); }
};

namespace DockConfigFile {
QString userPath();   // $TAOS_DOCK_CONFIG, else $XDG_CONFIG_HOME/taos/dock.json, else ~/.config/taos/dock.json
bool validPosition(const QString &v);
bool validSize(const QString &v);
bool validOutputs(const QString &v);
// Sanitising read: a bad value becomes the default. False when the file is missing or not a JSON object.
bool read(const QString &path, DockConfig *out, QString *error = nullptr);
// Atomic (temp file in the same folder, then rename); creates the folder.
bool write(const QString &path, const DockConfig &c, QString *error = nullptr);
// "true"/"false"/"on"/"off"/"yes"/"no"/"1"/"0".
bool parseBool(const QString &v, bool *out);
// Applies `set <key> <value>` to a config; false (with `error`) for an unknown key or a bad value.
bool setKey(DockConfig *c, const QString &key, const QString &value, QString *error = nullptr);
QString getKey(const DockConfig &c, const QString &key, bool *ok = nullptr);
}

// The live setting: reads dock.json, watches it (the file and its folder, because an atomic
// rename replaces the file) and tells everyone when it changes. Same behaviour as appearance.json.
class DockSettings : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString position READ position NOTIFY changed)
    Q_PROPERTY(bool attached READ attached NOTIFY changed)
    Q_PROPERTY(bool autohide READ autohide NOTIFY changed)
    Q_PROPERTY(QString size READ size NOTIFY changed)
    Q_PROPERTY(bool magnify READ magnify NOTIFY changed)
    Q_PROPERTY(bool hideInTiling READ hideInTiling NOTIFY changed)
    Q_PROPERTY(QString outputs READ outputs NOTIFY changed)
public:
    explicit DockSettings(const QString &path = QString(), bool watch = true, QObject *parent = nullptr);
    QString path() const { return m_path; }
    const DockConfig &config() const { return m_c; }
    QString position() const { return m_c.position; }
    bool attached() const { return m_c.attached; }
    bool autohide() const { return m_c.autohide; }
    QString size() const { return m_c.size; }
    bool magnify() const { return m_c.magnify; }
    bool hideInTiling() const { return m_c.hideInTiling; }
    QString outputs() const { return m_c.outputs; }

    // Writes the file first, then applies; false (and lastError) when the value is bad or the file cannot be written.
    Q_INVOKABLE bool set(const QString &key, const QString &value);
    bool setPinned(const QStringList &ids);
    void reload();
    QString lastError() const { return m_err; }

signals:
    void changed();
    void pinnedChanged();

private:
    void rewatch();
    bool commit(const DockConfig &c);
    QString m_path, m_err;
    DockConfig m_c;
    QFileSystemWatcher m_watch;
    QTimer m_debounce;
    bool m_watching;
};
