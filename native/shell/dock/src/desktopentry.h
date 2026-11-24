#pragma once
#include <QHash>
#include <QString>
#include <QStringList>
#include <QVector>

struct DesktopAction {
    QString id, name, exec;
};

// A freedesktop .desktop file (Desktop Entry spec), the part the dock needs.
struct DesktopEntry {
    QString id;               // file id without ".desktop" (subdirectories joined with "-")
    QString path;
    QString name, icon, exec, startupWMClass, taosAppId;
    bool noDisplay = false, hidden = false, terminal = false, singleMainWindow = false;
    QVector<DesktopAction> actions;
    // The action that opens a new window (new-window / NewWindow / a name "New Window"), or null.
    const DesktopAction *newWindowAction() const;
    bool valid() const { return !id.isEmpty() && !exec.isEmpty() && !name.isEmpty(); }
    // First word of Exec, as a base name ("env FOO=1 foot" gives "foot" after the env prefix).
    QString execBase() const;
};

namespace Desktop {
bool parse(const QByteArray &data, const QString &id, DesktopEntry *out);
// Exec line to program and arguments: field codes removed, quotes honoured.
bool commandLine(const QString &exec, QString *program, QStringList *args);
QStringList defaultDirs();   // $XDG_DATA_HOME/applications, then every $XDG_DATA_DIRS/applications
}

// Every launchable application, indexed for the dock.
class DesktopIndex {
public:
    // Earlier directories win (the person's own entries override the system's).
    void scan(const QStringList &dirs);
    void add(const DesktopEntry &e);     // tests and fixtures
    const DesktopEntry *byId(const QString &id) const;
    // The entry a window with this app_id belongs to: id, case-insensitive id, StartupWMClass,
    // the last segment of a reverse-DNS id, then Exec's program name. Null when none.
    const DesktopEntry *matchWindow(const QString &appId) const;
    const QVector<DesktopEntry> &all() const { return m_entries; }
private:
    QVector<DesktopEntry> m_entries;
    QHash<QString, int> m_byId;
};
