// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QFileSystemWatcher>
#include <QJsonObject>
#include <QObject>
#include <QTimer>
#include <memory>

#include "frecency.h"
#include "providers.h"
#include "types.h"

namespace taos::search {

// The nine categories of the app menu, in display order. Freedesktop categories are mapped onto them.
QStringList menuCategories();
QString menuCategoryFor(const DesktopEntry &e);

// One app as the menu shows it. `id` is the desktop file id ("firefox.desktop"), the same id the launcher's
// search results and frecency use ("app:" + id).
struct MenuApp {
    QString id, name, genericName, comment, icon;
    QString category;                    // one of menuCategories()
    QString group;                       // app group name ("My apps"); empty for apps on the host
    QString kind;                        // app | taos | group | web
    QString containerApp, webApp;        // ids for uninstall and move
    QStringList argv;
    bool terminal = false;
    bool pinned = false;
    bool valid() const { return !id.isEmpty(); }
};

struct MenuSection {
    QString title;
    QList<MenuApp> apps;
};

// The one model behind every layout of the app menu (centred panel, anchored start menu, ring, phone drawer),
// the CLI (`taos-search menu`) and the launcher's IPC. It reads the same desktop entries as the Apps search
// provider (one scan), keeps the pinned list, and follows installs and removals live.
class AppMenu : public QObject {
    Q_OBJECT
public:
    struct Paths {
        QString pinsPath;    // default ~/.config/taos/startmenu.json  {"pinned": ["id.desktop", ...]}
        QString dockPath;    // default ~/.config/taos/dock.json: its "pinned" list is the starting point until the menu has its own
    };
    static Paths defaultPaths();
    AppMenu(std::shared_ptr<AppsProvider> apps, std::shared_ptr<Frecency> frec, Paths paths = defaultPaths(), bool watch = true, QObject *parent = nullptr);

    QList<MenuApp> all() const;                  // every app, A-Z
    QList<MenuSection> byCategory() const;       // fixed category order, empty ones left out, apps A-Z
    QList<MenuSection> byLetter() const;         // A..Z then #
    QList<MenuApp> pinned() const;               // in the person's order; ids that are no longer installed are skipped
    QList<MenuApp> recent(int n = 6) const;      // most frecent, never a pinned app
    MenuApp find(const QString &id) const;       // accepts "x" and "x.desktop"
    bool isPinned(const QString &id) const;
    bool pin(const QString &id, QString *error = nullptr);
    bool unpin(const QString &id, QString *error = nullptr);
    bool movePinned(const QString &id, int toIndex, QString *error = nullptr);
    // Everything the person can do with an app, in menu order (Open first). Stubs say why.
    QList<Action> contextActions(const MenuApp &a) const;
    QStringList uninstallArgv(const MenuApp &a) const;   // empty: this app cannot be removed from here
    QJsonObject appJson(const MenuApp &a) const;
    QJsonObject toJson() const;                  // pinned, recent, categories, letters, count
    void refresh();                              // rescan now
    int count() const;
    // Normalised id with ".desktop"
    static QString normId(const QString &id);

signals:
    void changed();

private:
    MenuApp make(const DesktopEntry &e) const;
    QStringList pinnedIds() const;
    bool writePins(const QStringList &ids, QString *error);
    void watchDirs();
    std::shared_ptr<AppsProvider> m_apps;
    std::shared_ptr<Frecency> m_frec;
    Paths m_paths;
    QStringList m_pinsCache;
    bool m_pinsLoaded = false;
    QFileSystemWatcher m_watch;
    QTimer m_debounce;
    bool m_watching;
};

}  // namespace taos::search
