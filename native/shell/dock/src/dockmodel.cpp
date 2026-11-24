// SPDX-License-Identifier: AGPL-3.0-or-later
#include "dockmodel.h"

#include <algorithm>
#include <QMap>

bool DockItem::operator==(const DockItem &o) const {
    return kind == o.kind && key == o.key && desktopId == o.desktopId && name == o.name && icon == o.icon && title == o.title
        && pinned == o.pinned && running == o.running && focused == o.focused && launching == o.launching
        && canNewWindow == o.canNewWindow && windows == o.windows && minimisedCount == o.minimisedCount && windowId == o.windowId;
}

DockModel::DockModel(QObject *parent) : QAbstractListModel(parent) {}

void DockModel::setIndex(const DesktopIndex *idx) { m_idx = idx; rebuild(); }
void DockModel::setPinned(const QStringList &ids) { m_pinned = ids; rebuild(); }

void DockModel::setLaunching(const QString &key, bool on) {
    if (on == m_launching.contains(key)) return;
    if (on) m_launching.insert(key); else m_launching.remove(key);
    rebuild();
}

void DockModel::setWindows(const QVector<WindowInfo> &w) {
    m_windows = w;
    // Focus order: drop vanished windows, put the focused one first, remember new ones (newest first).
    QVector<int> kept;
    for (int id : std::as_const(m_mru)) if (window(id)) kept << id;
    m_mru = kept;
    QVector<int> fresh;
    for (const WindowInfo &x : w) if (!m_mru.contains(x.id)) fresh << x.id;
    std::sort(fresh.begin(), fresh.end(), std::greater<int>());
    m_mru += fresh;
    for (const WindowInfo &x : w)
        if (x.focused) { m_mru.removeAll(x.id); m_mru.prepend(x.id); }
    rebuild();
}

const WindowInfo *DockModel::window(int id) const {
    for (const WindowInfo &w : m_windows) if (w.id == id) return &w;
    return nullptr;
}

QVector<int> DockModel::mruOf(const DockItem &it) const {
    QVector<int> r;
    for (int id : m_mru) if (it.windows.contains(id)) r << id;
    for (int id : it.windows) if (!r.contains(id)) r << id;
    return r;
}

void DockModel::rebuild() {
    // Group windows by application.
    struct Group { QVector<WindowInfo> w; QString appId; const DesktopEntry *e = nullptr; };
    QMap<QString, Group> groups;
    for (const WindowInfo &w : std::as_const(m_windows)) {
        const DesktopEntry *e = m_idx ? m_idx->matchWindow(w.appId) : nullptr;
        const QString key = e ? e->id : QStringLiteral("app:") + w.appId.toLower();
        Group &g = groups[key];
        if (g.w.isEmpty()) { g.appId = w.appId; g.e = e; }
        g.w << w;
    }
    auto fill = [&](DockItem &it, const Group *g) {
        if (!g) return;
        it.running = !g->w.isEmpty();
        for (const WindowInfo &w : g->w) {
            it.windows << w.id;
            if (w.focused && !w.minimised) it.focused = true;
            if (w.minimised) ++it.minimisedCount;
        }
        std::sort(it.windows.begin(), it.windows.end());
    };

    QVector<DockItem> out;
    QSet<QString> used;
    for (const QString &id : std::as_const(m_pinned)) {
        const DesktopEntry *e = m_idx ? m_idx->byId(id) : nullptr;
        if (!e || used.contains(e->id)) continue;
        used.insert(e->id);
        DockItem it;
        it.key = it.desktopId = e->id;
        it.name = e->name;
        it.icon = e->icon;
        it.pinned = true;
        it.canNewWindow = e->newWindowAction() || !e->singleMainWindow;
        fill(it, groups.contains(e->id) ? &groups[e->id] : nullptr);
        out << it;
    }
    QVector<DockItem> extra;
    for (auto g = groups.constBegin(); g != groups.constEnd(); ++g) {
        if (used.contains(g.key())) continue;
        DockItem it;
        it.key = g.key();
        if (g->e) {
            it.desktopId = g->e->id;
            it.name = g->e->name;
            it.icon = g->e->icon;
            it.canNewWindow = g->e->newWindowAction() || !g->e->singleMainWindow;
        } else {
            it.name = g->appId;
            it.icon = g->appId;
        }
        fill(it, &*g);
        extra << it;
    }
    std::sort(extra.begin(), extra.end(), [](const DockItem &a, const DockItem &b) { return a.windows.first() < b.windows.first(); });
    out += extra;
    // Launch pulse: ends as soon as the app has a window.
    QStringList cleared;
    for (DockItem &it : out) {
        if (m_launching.contains(it.key)) {
            if (it.running) cleared << it.key;
            else it.launching = true;
        }
    }
    for (const QString &k : std::as_const(cleared)) m_launching.remove(k);

    QVector<DockItem> mins;
    for (const WindowInfo &w : std::as_const(m_windows)) {
        if (!w.minimised) continue;
        const DesktopEntry *e = m_idx ? m_idx->matchWindow(w.appId) : nullptr;
        DockItem it;
        it.kind = DockItem::Minimised;
        it.key = QStringLiteral("win:%1").arg(w.id);
        it.windowId = w.id;
        it.title = w.title;
        it.name = e ? e->name : w.appId;
        it.icon = e ? e->icon : w.appId;
        it.desktopId = e ? e->id : QString();
        it.windows << w.id;
        it.running = true;
        it.minimisedCount = 1;
        mins << it;
    }
    std::sort(mins.begin(), mins.end(), [](const DockItem &a, const DockItem &b) { return a.windowId < b.windowId; });
    if (!mins.isEmpty()) {
        DockItem sep;
        sep.kind = DockItem::Separator;
        sep.key = QStringLiteral("separator");
        out << sep;
        out += mins;
    }

    bool sameKeys = out.size() == m_items.size();
    for (int i = 0; sameKeys && i < out.size(); ++i) sameKeys = out[i].key == m_items[i].key;
    const int oldCount = m_items.size();
    if (sameKeys) {
        for (int i = 0; i < out.size(); ++i) {
            if (out[i] == m_items[i]) continue;
            m_items[i] = out[i];
            emit dataChanged(index(i), index(i));
        }
    } else {
        beginResetModel();
        m_items = out;
        endResetModel();
    }
    const int pc = pinnedCount();
    if (oldCount != m_items.size() || pc != m_lastPinned) { m_lastPinned = pc; emit countChanged(); }
    for (const QString &k : std::as_const(cleared)) emit launchCleared(k);
}

int DockModel::separatorCount() const {
    int n = 0;
    for (const DockItem &i : m_items) if (i.kind == DockItem::Separator) ++n;
    return n;
}

int DockModel::pinnedCount() const {
    int n = 0;
    for (const DockItem &i : m_items) if (i.kind == DockItem::App && i.pinned) ++n;
    return n;
}

int DockModel::rowForKey(const QString &key) const {
    for (int i = 0; i < m_items.size(); ++i) if (m_items[i].key == key) return i;
    return -1;
}

QString DockModel::accessibleName(const DockItem &it) {
    if (it.kind == DockItem::Separator) return {};
    if (it.kind == DockItem::Minimised)
        return QStringLiteral("%1, minimised window of %2").arg(it.title.isEmpty() ? it.name : it.title, it.name);
    QString s = it.name;
    if (!it.running) {
        s += it.launching ? QStringLiteral(", opening") : QStringLiteral(", not running");
    } else {
        const int n = it.windows.size();
        s += n == 1 ? QStringLiteral(", running, 1 window") : QStringLiteral(", running, %1 windows").arg(n);
        if (it.minimisedCount > 0) s += QStringLiteral(", %1 minimised").arg(it.minimisedCount);
        if (it.focused) s += QStringLiteral(", focused");
    }
    return s;
}

QVariant DockModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() < 0 || i.row() >= m_items.size()) return {};
    const DockItem &it = m_items[i.row()];
    switch (role) {
    case KindRole: return it.kind == DockItem::App ? QStringLiteral("app") : it.kind == DockItem::Separator ? QStringLiteral("separator") : QStringLiteral("minimised");
    case KeyRole: return it.key;
    case NameRole: return it.name;
    case IconRole: return it.icon;
    case PinnedRole: return it.pinned;
    case RunningRole: return it.running;
    case FocusedRole: return it.focused;
    case LaunchingRole: return it.launching;
    case WindowCountRole: return it.windows.size();
    case MinimisedCountRole: return it.minimisedCount;
    case AccessibleRole: return accessibleName(it);
    case TooltipRole: return it.kind == DockItem::Minimised ? (it.title.isEmpty() ? it.name : it.title) : it.name;
    case WindowIdRole: return it.windowId;
    case TitleRole: return it.title;
    default: return {};
    }
}

QHash<int, QByteArray> DockModel::roleNames() const {
    return {{KindRole, "kind"}, {KeyRole, "key"}, {NameRole, "name"}, {IconRole, "icon"}, {PinnedRole, "pinned"},
            {RunningRole, "running"}, {FocusedRole, "focused"}, {LaunchingRole, "launching"},
            {WindowCountRole, "windowCount"}, {MinimisedCountRole, "minimisedCount"}, {AccessibleRole, "accessibleText"},
            {TooltipRole, "tooltip"}, {WindowIdRole, "windowId"}, {TitleRole, "title"}};
}

DockAction DockModel::primaryAction(int row) const {
    DockAction a;
    if (row < 0 || row >= m_items.size()) return a;
    const DockItem &it = m_items[row];
    a.key = it.key;
    a.desktopId = it.desktopId;
    if (it.kind == DockItem::Separator) return a;
    if (it.kind == DockItem::Minimised) { a.type = DockAction::Restore; a.windowId = it.windowId; return a; }
    if (it.windows.isEmpty()) {
        if (!it.desktopId.isEmpty() && !it.launching) a.type = DockAction::Launch;
        return a;
    }
    auto toward = [&](int id) {
        const WindowInfo *w = window(id);
        a.windowId = id;
        a.type = w && w->minimised ? DockAction::Restore : DockAction::Focus;
    };
    if (!it.focused) { toward(mruOf(it).first()); return a; }
    if (it.windows.size() == 1) { a.type = DockAction::Minimise; a.windowId = it.windows.first(); return a; }
    // Several windows and one has focus: cycle through them in a fixed order.
    int cur = 0;
    for (int i = 0; i < it.windows.size(); ++i) {
        const WindowInfo *w = window(it.windows[i]);
        if (w && w->focused && !w->minimised) { cur = i; break; }
    }
    toward(it.windows[(cur + 1) % it.windows.size()]);
    return a;
}

DockAction DockModel::middleAction(int row) const {
    DockAction a;
    if (row < 0 || row >= m_items.size()) return a;
    const DockItem &it = m_items[row];
    a.key = it.key;
    a.desktopId = it.desktopId;
    if (it.kind != DockItem::App || it.desktopId.isEmpty()) return a;
    if (it.windows.isEmpty()) { a.type = it.launching ? DockAction::None : DockAction::Launch; return a; }
    if (it.canNewWindow) a.type = DockAction::NewWindow;
    return a;
}

static DockMenuRow sep() { DockMenuRow r; r.kind = DockMenuRow::Separator; return r; }
static DockMenuRow entry(const QString &id, const QString &text, bool enabled = true, bool danger = false) {
    DockMenuRow r; r.id = id; r.text = text; r.enabled = enabled; r.danger = danger; return r;
}

static QVector<DockMenuSub> subsFor(const WindowInfo &w, const QString &title) {
    QVector<DockMenuSub> v;
    const bool big = w.fullscreen || w.fullSize;
    auto add = [&](const QString &id, const QString &glyph, const QString &verb, bool enabled = true) {
        DockMenuSub s;
        s.id = id + QString::number(w.id);
        s.glyph = glyph;
        s.accessible = verb + QLatin1Char(' ') + title;
        s.enabled = enabled;
        v << s;
    };
    add(QStringLiteral("window-min:"), w.minimised ? QStringLiteral("restore") : QStringLiteral("min"), w.minimised ? QStringLiteral("Restore") : QStringLiteral("Minimise"));
    add(QStringLiteral("window-full:"), big ? QStringLiteral("unfull") : QStringLiteral("full"), big ? QStringLiteral("Restore size of") : QStringLiteral("Full size"), !w.minimised);
    add(QStringLiteral("window-close:"), QStringLiteral("close"), QStringLiteral("Close"));
    return v;
}

// Window rows: a title that focuses (or restores) the window, with the three actions inline.
QVector<DockMenuRow> DockModel::windowListFor(int row) const {
    QVector<DockMenuRow> rows;
    if (row < 0 || row >= m_items.size()) return rows;
    const DockItem &it = m_items[row];
    if (it.kind == DockItem::Separator) return rows;
    for (int id : it.windows) {
        const WindowInfo *w = window(id);
        if (!w) continue;
        const QString title = w->title.isEmpty() ? it.name : w->title;
        DockMenuRow r = entry(QStringLiteral("window:%1").arg(id), title);
        r.windowId = id;
        r.minimised = w->minimised;
        r.accessible = (w->minimised ? QStringLiteral("Restore ") : QStringLiteral("Show ")) + title;
        r.subs = subsFor(*w, title);
        rows << r;
    }
    return rows;
}

QVector<DockMenuRow> DockModel::menuFor(int row) const {
    QVector<DockMenuRow> rows;
    const bool onItem = row >= 0 && row < m_items.size() && m_items[row].kind != DockItem::Separator;
    if (onItem) {
        const DockItem &it = m_items[row];
        if (it.kind == DockItem::Minimised) {
            rows << entry(QStringLiteral("restore"), QStringLiteral("Restore"));
            rows.last().windowId = it.windowId;
            rows.last().accessible = QStringLiteral("Restore ") + it.name;
            rows << entry(QStringLiteral("close"), QStringLiteral("Close window"), true, true);
            rows.last().windowId = it.windowId;
            rows.last().accessible = QStringLiteral("Close ") + it.name;
        } else {
            if (it.windows.size() == 1) {
                // One window: its actions go straight at the top of the menu.
                const WindowInfo *w = window(it.windows.first());
                if (w) {
                    const bool big = w->fullscreen || w->fullSize;
                    auto act = [&](const QString &id, const QString &text, const QString &verb, bool enabled, bool danger) {
                        DockMenuRow r = entry(id + QString::number(w->id), text, enabled, danger);
                        r.windowId = w->id;
                        r.accessible = verb + QLatin1Char(' ') + it.name;
                        rows << r;
                    };
                    act(QStringLiteral("window-min:"), w->minimised ? QStringLiteral("Restore") : QStringLiteral("Minimise"), w->minimised ? QStringLiteral("Restore") : QStringLiteral("Minimise"), true, false);
                    act(QStringLiteral("window-full:"), big ? QStringLiteral("Restore size") : QStringLiteral("Full size"), big ? QStringLiteral("Restore size of") : QStringLiteral("Full size"), !w->minimised, false);
                    act(QStringLiteral("window-close:"), QStringLiteral("Close"), QStringLiteral("Close"), true, true);
                }
            } else if (it.windows.size() > 1) {
                rows += windowListFor(row);
                bool anyUp = false;
                for (int id : it.windows) if (const WindowInfo *w = window(id)) anyUp |= !w->minimised;
                rows << sep();
                rows << entry(QStringLiteral("minimise-all"), QStringLiteral("Minimise all"), anyUp);
                rows.last().accessible = QStringLiteral("Minimise all ") + it.name;
            }
            if (!it.windows.isEmpty()) rows << sep();
            const DesktopEntry *e = m_idx && !it.desktopId.isEmpty() ? m_idx->byId(it.desktopId) : nullptr;
            if (e && e->newWindowAction()) rows << entry(QStringLiteral("new-window"), QStringLiteral("New window"));
            if (it.pinned) rows << entry(QStringLiteral("unpin"), QStringLiteral("Remove from Dock"));
            else rows << entry(QStringLiteral("pin"), QStringLiteral("Keep in Dock"), !it.desktopId.isEmpty());
            if (it.windows.size() > 1) {
                rows << entry(QStringLiteral("quit"), QStringLiteral("Close all (%1 windows)").arg(it.windows.size()), true, true);
                rows.last().accessible = QStringLiteral("Close all windows of ") + it.name;
            }
        }
        rows << sep();
    }
    rows << entry(QStringLiteral("settings"), QStringLiteral("Dock settings"));
    return rows;
}

QStringList DockModel::pinnedAfterMove(int from, int to) const {
    QStringList shown;
    for (const DockItem &i : m_items) if (i.kind == DockItem::App && i.pinned) shown << i.key;
    if (from < 0 || to < 0 || from >= shown.size() || to >= shown.size() || from == to) return m_pinned;
    shown.move(from, to);
    QStringList result = shown;
    for (const QString &id : m_pinned) if (!shown.contains(id) && !result.contains(id)) result << id;
    return result;
}

QStringList DockModel::pinnedWith(const QString &id) const {
    QStringList l = m_pinned;
    if (!l.contains(id)) l << id;
    return l;
}

QStringList DockModel::pinnedWithout(const QString &id) const {
    QStringList l = m_pinned;
    l.removeAll(id);
    return l;
}
