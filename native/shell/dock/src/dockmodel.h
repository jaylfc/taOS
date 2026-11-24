// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>
#include <QSet>
#include <QVector>

#include "desktopentry.h"
#include "windowinfo.h"

struct DockItem {
    enum Kind { App, Separator, Minimised };
    Kind kind = App;
    QString key;              // desktop id; "app:<app_id>" for a window without an entry; "win:<id>" for a minimised window
    QString desktopId;        // empty when there is no .desktop entry
    QString name, icon, title;
    bool pinned = false, running = false, focused = false, launching = false;
    bool canNewWindow = false;   // a second window can be asked for (an action, or an app that allows several)
    QVector<int> windows;     // every window of the app, minimised ones included, by id
    int minimisedCount = 0;
    int windowId = 0;         // Minimised: its window
    bool operator==(const DockItem &o) const;
};

// What a click decided.
struct DockAction {
    enum Type { None, Launch, Focus, Restore, Minimise, NewWindow, CloseAll, CloseWindow } type = None;
    int windowId = 0;
    QString desktopId, key;
    bool operator==(const DockAction &o) const { return type == o.type && windowId == o.windowId && desktopId == o.desktopId; }
};

// A small button inside a window row (several windows): minimise or restore, full size, close.
struct DockMenuSub {
    QString id;               // window-min:<id> window-full:<id> window-close:<id>
    QString glyph;            // min | restore | full | unfull | close (the QML draws these)
    QString accessible;       // "Minimise Activity: Models"
    bool enabled = true;
};

struct DockMenuRow {
    enum Kind { Entry, Separator } kind = Entry;
    QString id;               // window:<id> window-min:<id> window-full:<id> window-close:<id> minimise-all new-window pin unpin quit settings restore close
    QString text;
    QString accessible;       // the name a screen reader gets (defaults to text)
    QVector<DockMenuSub> subs;
    int windowId = 0;
    bool enabled = true, danger = false, minimised = false;
};

// The dock's content, derived ONLY from three inputs: the pinned list, the installed applications
// and the compositor's window list (minimised windows included). Nothing about minimised windows is
// kept here, so a restarted dock shows exactly what the compositor says. The only memory is cosmetic:
// the focus order (which window "most recent" means) and the launch pulse.
class DockModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
    Q_PROPERTY(int pinnedCount READ pinnedCount NOTIFY countChanged)
public:
    enum Role { KindRole = Qt::UserRole + 1, KeyRole, NameRole, IconRole, PinnedRole, RunningRole, FocusedRole, LaunchingRole,
                WindowCountRole, MinimisedCountRole, AccessibleRole, TooltipRole, WindowIdRole, TitleRole };
    explicit DockModel(QObject *parent = nullptr);

    void setIndex(const DesktopIndex *idx);
    void setPinned(const QStringList &ids);
    void setWindows(const QVector<WindowInfo> &w);
    void setLaunching(const QString &key, bool on);
    bool launching(const QString &key) const { return m_launching.contains(key); }

    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : m_items.size(); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    const QVector<DockItem> &items() const { return m_items; }
    const QVector<WindowInfo> &windows() const { return m_windows; }
    const WindowInfo *window(int id) const;
    int separatorCount() const;
    int pinnedCount() const;                   // pinned rows shown
    int rowForKey(const QString &key) const;

    // Click semantics (the state machine). `middle` is the middle button.
    DockAction primaryAction(int row) const;
    DockAction middleAction(int row) const;
    QVector<DockMenuRow> menuFor(int row) const;        // right click; row -1 is the strip itself
    QVector<DockMenuRow> windowListFor(int row) const;  // long press
    // The pinned list after dragging row `from` to row `to` (both pinned rows); hidden ids keep their place at the end.
    QStringList pinnedAfterMove(int from, int to) const;
    // Pinned list with `id` added (end) or removed.
    QStringList pinnedWith(const QString &id) const;
    QStringList pinnedWithout(const QString &id) const;
    QStringList pinnedList() const { return m_pinned; }

    static QString accessibleName(const DockItem &it);

signals:
    void countChanged();
    void launchCleared(const QString &key);

private:
    void rebuild();
    QVector<int> mruOf(const DockItem &it) const;
    QStringList m_pinned;
    const DesktopIndex *m_idx = nullptr;
    QVector<WindowInfo> m_windows;
    QVector<DockItem> m_items;
    int m_lastPinned = -1;
    QVector<int> m_mru;                         // window ids, most recent first (cosmetic memory only)
    QSet<QString> m_launching;
};
