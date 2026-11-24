#pragma once
#include <QFileSystemWatcher>
#include <QJsonObject>
#include <QObject>
#include <QTimer>

namespace TaosSettings {

// A person's JSON settings file that another program (the dock, the window engine) owns the meaning of.
// Reads the whole object, changes only the keys it is asked to, and writes it back atomically, so keys this
// app does not know (the dock's `pinned` list) survive. Watches the file and its folder, so a change made
// by the CLI or another window shows up at once.
class JsonFile : public QObject {
    Q_OBJECT
public:
    explicit JsonFile(const QString &path, bool watch = true, QObject *parent = nullptr);
    QString path() const { return m_path; }
    const QJsonObject &object() const { return m_obj; }
    bool exists() const { return m_exists; }
    QString lastError() const { return m_error; }
    // Re-read; emits changed() when the content differs.
    void reload();
    // Merge `patch` into the file and write it. Keys with a null value are removed.
    bool merge(const QJsonObject &patch);
signals:
    void changed();
private:
    void rewatch();
    QString m_path, m_error;
    QJsonObject m_obj;
    bool m_exists = false;
    QFileSystemWatcher m_watch;
    QTimer m_debounce;
};

// ~/.config/taos/dock.json, owned by taos-dock. Seen only through the file.
class DockSettings : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString position READ position NOTIFY changed)
    Q_PROPERTY(bool attached READ attached NOTIFY changed)
    Q_PROPERTY(bool autohide READ autohide NOTIFY changed)
    Q_PROPERTY(QString size READ size NOTIFY changed)
    Q_PROPERTY(bool magnify READ magnify NOTIFY changed)
    Q_PROPERTY(bool hideInTiling READ hideInTiling NOTIFY changed)
    Q_PROPERTY(bool dockInstalled READ dockInstalled CONSTANT)
    Q_PROPERTY(QString errorText READ errorText NOTIFY changed)
public:
    static QString defaultPath();   // $TAOS_DOCK_CONFIG, else $XDG_CONFIG_HOME/taos/dock.json
    explicit DockSettings(const QString &path = defaultPath(), bool watch = true, QObject *parent = nullptr);
    QString position() const;
    bool attached() const;
    bool autohide() const;
    QString size() const;
    bool magnify() const;
    bool hideInTiling() const;
    bool dockInstalled() const;
    QString errorText() const { return m_file.lastError(); }
    // key: position, attached, autohide, size, magnify, hideInTiling. False for a bad key or value, or a write error.
    Q_INVOKABLE bool set(const QString &key, const QVariant &value);
    JsonFile *file() { return &m_file; }
signals:
    void changed();
private:
    JsonFile m_file;
};

// ~/.config/taos/desktop.json, owned by taos-window: {"default": "desktop", "workspaces": {"2": "tiling"}}.
class DesktopSettings : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString defaultMode READ defaultMode NOTIFY changed)
    Q_PROPERTY(QVariantList workspaces READ workspaces NOTIFY changed)
    Q_PROPERTY(QString errorText READ errorText NOTIFY changed)
public:
    static QString defaultPath();   // TaosWin::Modes::path()
    explicit DesktopSettings(const QString &path = defaultPath(), bool watch = true, QObject *parent = nullptr);
    QString defaultMode() const;
    QVariantList workspaces() const;   // [{name, mode}] the ones that differ from the default
    QString errorText() const { return m_file.lastError(); }
    Q_INVOKABLE bool setDefaultMode(const QString &mode);
    // Remove a workspace's own setting so it follows the default again.
    Q_INVOKABLE bool useDefaultFor(const QString &workspace);
signals:
    void changed();
private:
    JsonFile m_file;
};

} // namespace TaosSettings
