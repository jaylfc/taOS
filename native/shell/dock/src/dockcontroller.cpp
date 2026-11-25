// SPDX-License-Identifier: AGPL-3.0-or-later
#include "dockcontroller.h"

#include <QDir>
#include <QDirIterator>

#include <QProcess>
#include <QStandardPaths>
#include <QVariantMap>
#include <cstdio>

#include "cliengine.h"
#include "swayengine.h"

static QVariantList toVariant(const QVector<DockMenuRow> &rows);

QStringList DockController::defaultPinned(const DesktopIndex &idx) {
    // Files, Terminal, Browser, Activity, Notifications, Decisions, Agents, Messages, Settings: first existing candidate of each slot.
    const QVector<QStringList> candidates = {
        {QStringLiteral("my.taos.files")},
        {QStringLiteral("foot"), QStringLiteral("my.taos.terminal")},
        {QStringLiteral("my.taos.browser"), QStringLiteral("chromium"), QStringLiteral("org.chromium.Chromium"), QStringLiteral("firefox")},
        {QStringLiteral("my.taos.dashboard")},
        {QStringLiteral("my.taos.notifications")},
        {QStringLiteral("my.taos.decisions")},
        {QStringLiteral("my.taos.agents")},
        {QStringLiteral("my.taos.messages")},
        {QStringLiteral("my.taos.settings")},
    };
    QStringList out;
    for (const QStringList &s : candidates)
        for (const QString &id : s)
            if (const DesktopEntry *e = idx.byId(id)) { out << e->id; break; }
    return out;
}

DockController::DockController(const Options &o, QObject *parent)
    : QObject(parent), m_o(o), m_settings(o.configPath, o.watch), m_modes(o.modePath, o.watch) {
    if (m_o.appDirs.isEmpty()) m_o.appDirs = Desktop::defaultDirs();
    // The real window engine when it is installed; the built-in fallback only when it is absent.
    if (m_o.engine == QLatin1String("auto"))
        m_o.engine = QStandardPaths::findExecutable(m_o.cliProgram).isEmpty() ? QStringLiteral("sway") : QStringLiteral("cli");
    if (m_o.engine == QLatin1String("cli")) m_engine = new CliEngine(m_o.cliProgram, this);
    else m_engine = new SwayEngine(m_o.swaySocket, this);   // FALLBACK engine, see windowengine.h
    m_spawn = [](const QString &prog, const QStringList &args) { QProcess::startDetached(prog, args); };
    m_hideTimer.setSingleShot(true);
    m_hideTimer.setInterval(450);
    connect(&m_hideTimer, &QTimer::timeout, this, &DockController::updateReveal);
    m_shrinkTimer.setSingleShot(true);
    m_shrinkTimer.setInterval(qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1") ? 0 : 240);   // after the slide-out
    connect(&m_shrinkTimer, &QTimer::timeout, this, [this] {
        if (!m_revealed && !m_shrunk) { m_shrunk = true; emit shrunkChanged(); }
    });
    connect(&m_settings, &DockSettings::changed, this, &DockController::relayout);
    connect(&m_modes, &DesktopModeWatcher::changed, this, &DockController::relayout);
    connect(m_engine, &WindowEngine::focusedWorkspaceChanged, this, &DockController::relayout);
    connect(&m_settings, &DockSettings::pinnedChanged, this, &DockController::refreshModel);
    connect(&m_model, &DockModel::countChanged, this, &DockController::relayout);
    connect(m_engine, &WindowEngine::spacesChanged, this, [this] {
        m_model.setSpaces(m_engine->spaces());
        if (!m_menuOpen || !m_menuBuild) return;          // an open menu follows: a space was made or went away
        m_menu = m_menuBuild();
        m_menuRows = toVariant(m_menu);
        emit menuRowsChanged();
    });
    connect(m_engine, &WindowEngine::windowsChanged, this, [this] { m_model.setWindows(m_engine->windows()); m_taskbar.setWindows(m_engine->windows()); relayout(); });
    m_index.scan(m_o.appDirs);
    if (m_o.watch) {
        m_appDebounce.setSingleShot(true);
        m_appDebounce.setInterval(300);   // an install writes several files; rescan once it settles
        connect(&m_appDebounce, &QTimer::timeout, this, &DockController::rescanApps);
        connect(&m_appWatch, &QFileSystemWatcher::directoryChanged, this, [this] { m_appDebounce.start(); });
        watchAppDirs();
    }
    m_model.setIndex(&m_index);
    m_taskbar.setIndex(&m_index);
    refreshModel();
    relayout();
}

// Watches every application folder and its subfolders; a folder that does not exist yet is watched through its
// nearest existing parent, so the first entry the person installs is noticed too.
void DockController::watchAppDirs() {
    QStringList want;
    for (const QString &d : std::as_const(m_o.appDirs)) {
        QDir dir(d);
        while (!dir.exists() && dir.cdUp()) {}
        if (!dir.exists()) continue;
        want << dir.absolutePath();
        QDirIterator it(dir.absolutePath(), QDir::Dirs | QDir::NoDotAndDotDot, QDirIterator::Subdirectories);
        if (dir.absolutePath() == QDir(d).absolutePath())
            while (it.hasNext()) want << it.next();
    }
    want.removeDuplicates();
    const QStringList have = m_appWatch.directories();
    QStringList gone;
    for (const QString &h : have) if (!want.contains(h)) gone << h;
    if (!gone.isEmpty()) m_appWatch.removePaths(gone);
    QStringList add;
    for (const QString &w : std::as_const(want)) if (!have.contains(w)) add << w;
    if (!add.isEmpty()) m_appWatch.addPaths(add);
}

void DockController::rescanApps() {
    m_index.scan(m_o.appDirs);
    watchAppDirs();
    m_model.setIndex(&m_index);          // the old entry pointers are gone: rebuild from the new index
    m_taskbar.setWindows(m_engine->windows());
    refreshModel();
    relayout();
}

void DockController::start() { m_engine->start(); }

void DockController::refreshModel() {
    const DockConfig &c = m_settings.config();
    m_model.setPinned(c.pinnedSet ? c.pinned : defaultPinned(m_index));
}

QString DockController::currentWorkspace() const {
    QString ws = m_engine->focusedWorkspace();
    if (ws.isEmpty())   // an engine that cannot say (the CLI one): the workspace of the focused window
        for (const WindowInfo &w : m_model.windows()) if (w.focused && !w.minimised) { ws = w.workspace; break; }
    return ws;
}

bool DockController::tilingNow() const { return m_modes.modes().tiling(currentWorkspace()); }

bool DockController::effectiveAutohide() const {
    return m_settings.autohide() || (m_settings.hideInTiling() && tilingNow());
}

void DockController::setOutputSize(const QSize &s) { m_output = s; relayout(); }

void DockController::relayout() {
    LayoutInput in;
    in.position = m_settings.position();
    in.size = m_settings.size();
    in.attached = m_settings.attached();
    in.autohide = effectiveAutohide();
    in.magnify = m_settings.magnify();
    in.items = 0;
    for (const DockItem &i : m_model.items()) if (i.kind != DockItem::Separator) ++in.items;
    in.separators = m_model.separatorCount();
    in.output = m_output;
    m_layout = DockLayout::compute(in);
    emit layoutChanged();
    updateReveal();
}

void DockController::updateReveal() {
    const bool want = !effectiveAutohide() || m_inside || m_menuOpen || m_kbd;
    if (want == m_revealed) return;
    if (!want && (m_inside || m_menuOpen)) return;
    m_revealed = want;
    if (want) {
        m_shrinkTimer.stop();
        if (m_shrunk) { m_shrunk = false; emit shrunkChanged(); }   // full size first, then the strip slides in
    } else {
        m_shrinkTimer.start();
    }
    emit revealedChanged();
}

void DockController::setPointerInside(bool inside) {
    m_inside = inside;
    if (inside) { m_hideTimer.stop(); updateReveal(); }
    else m_hideTimer.start();
}

void DockController::showTip(const QString &text, qreal x, qreal y) { emit tipRequested(text, x, y); }
void DockController::hideTip() { emit tipHidden(); }

void DockController::grabKeyboard() {
    m_kbd = true;
    reveal();
    emit keyboardActiveChanged();
}

void DockController::releaseKeyboard() {
    if (!m_kbd) return;
    m_kbd = false;
    emit keyboardActiveChanged();
    m_inside = false;
    m_hideTimer.start();
}

void DockController::handleCommand(const QString &w) {
    if (w == QLatin1String("reveal")) reveal();
    else if (w == QLatin1String("hide")) { releaseKeyboard(); hideNow(); }
    else if (w == QLatin1String("toggle")) { if (m_revealed && effectiveAutohide()) { releaseKeyboard(); hideNow(); } else reveal(); }
    else if (w == QLatin1String("focus")) { if (m_kbd) releaseKeyboard(); else grabKeyboard(); }
}

void DockController::reveal() { m_inside = true; updateReveal(); m_hideTimer.stop(); }
void DockController::hideNow() { m_inside = false; updateReveal(); }

bool DockController::launch(const QString &desktopId, bool newWindow) {
    const DesktopEntry *e = m_index.byId(desktopId);
    if (!e) return false;
    QString exec = e->exec;
    if (newWindow)
        if (const DesktopAction *a = e->newWindowAction()) exec = a->exec;
    QString prog;
    QStringList args;
    if (!Desktop::commandLine(exec, &prog, &args)) return false;
    if (e->terminal) { args.prepend(prog); prog = QStringLiteral("foot"); }
    m_spawn(prog, args);
    const int r = m_model.rowForKey(e->id);
    if (r < 0 || m_model.items()[r].windows.isEmpty()) {
        m_model.setLaunching(e->id, true);
        QTimer::singleShot(10000, this, [this, id = e->id] { m_model.setLaunching(id, false); });
    }
    return true;
}

void DockController::perform(const DockAction &a) {
    switch (a.type) {
    case DockAction::None: break;
    case DockAction::Launch: launch(a.desktopId, false); break;
    case DockAction::NewWindow: launch(a.desktopId, true); break;
    case DockAction::Focus: m_engine->focus(a.windowId); break;
    case DockAction::Restore: m_engine->restore(a.windowId); break;
    case DockAction::Minimise: m_engine->minimise(a.windowId); break;
    case DockAction::CloseWindow: m_engine->close(a.windowId); break;
    case DockAction::CloseAll: {
        const int r = m_model.rowForKey(a.key);
        if (r >= 0) for (int id : m_model.items()[r].windows) m_engine->close(id);
        break;
    }
    }
}

void DockController::activate(int row) { perform(m_model.primaryAction(row)); }
void DockController::middleClick(int row) { perform(m_model.middleAction(row)); }

// A window control row of a menu. Each one is a single engine command; the engine owns every geometry rule.
//   window-min:<id>        Minimise, or Restore a minimised window
//   window-full:<id>       Maximise, or (already maximised) Restore size
//   window-fs:<id>         Full screen, or Exit full screen
//   window-bring:<id>  window-reset:<id>  window-close:<id>  window-newspace:<id>
//   window-space:<id>:<space>
void DockController::windowAction(const QString &id) {
    const int colon = id.indexOf(QLatin1Char(':'));
    const QString verb = id.left(colon);
    const QString rest = id.mid(colon + 1);
    const int colon2 = rest.indexOf(QLatin1Char(':'));      // only window-space has a second one: the space name may hold any text
    const int win = rest.left(colon2 < 0 ? rest.size() : colon2).toInt();
    const WindowInfo *w = m_model.window(win);
    if (verb == QLatin1String("window-min")) { if (w && w->minimised) m_engine->restore(win); else m_engine->minimise(win); }
    else if (verb == QLatin1String("window-full")) { if (w && !w->minimised) { if (w->fullSize) m_engine->restoreSize(win); else m_engine->maximise(win); } }
    else if (verb == QLatin1String("window-fs")) { if (w && !w->minimised) m_engine->fullscreen(win, !w->fullscreen); }
    else if (verb == QLatin1String("window-bring")) m_engine->bringHere(win);
    else if (verb == QLatin1String("window-reset")) m_engine->resetSize(win);
    else if (verb == QLatin1String("window-space") && colon2 >= 0) m_engine->moveToSpace(win, rest.mid(colon2 + 1));
    else if (verb == QLatin1String("window-newspace")) m_engine->newSpace(win);
    else if (verb == QLatin1String("window-close")) m_engine->close(win);
}

static QVariantList toVariant(const QVector<DockMenuRow> &rows) {
    QVariantList l;
    for (const DockMenuRow &r : rows) {
        QVariantMap m;
        m[QStringLiteral("separator")] = r.kind == DockMenuRow::Separator;
        m[QStringLiteral("text")] = r.text;
        m[QStringLiteral("id")] = r.id;
        m[QStringLiteral("enabled")] = r.enabled;
        m[QStringLiteral("danger")] = r.danger;
        m[QStringLiteral("minimised")] = r.minimised;
        m[QStringLiteral("current")] = r.current;
        m[QStringLiteral("accessible")] = r.accessible.isEmpty() ? r.text : r.accessible;
        m[QStringLiteral("children")] = toVariant(r.children);
        // Enter or a click on this row opens its submenu (the window rows of an app with several windows run: they focus it)
        m[QStringLiteral("opens")] = !r.children.isEmpty() && !r.id.startsWith(QLatin1String("window:"));
        l << m;
    }
    return l;
}

void DockController::showMenu(std::function<QVector<DockMenuRow>()> build, qreal x, qreal y, int row) {
    m_menuBuild = std::move(build);
    m_menu = m_menuBuild();
    m_menuRow = row;
    m_menuRows = toVariant(m_menu);
    m_menuOpen = true;
    updateReveal();
    emit menuRowsChanged();
    emit menuRequested(x, y, row);
    m_engine->refreshSpaces();      // the list of spaces may have changed since the last look; the menu follows when it arrives
}

void DockController::openMenu(int row, qreal x, qreal y) {
    showMenu([this, row] { return m_model.menuFor(row); }, x, y, row);
}

void DockController::openWindowList(int row, qreal x, qreal y) {
    if (m_model.windowListFor(row).isEmpty()) { openMenu(row, x, y); return; }
    showMenu([this, row] { return m_model.windowListFor(row); }, x, y, row);
}

void DockController::openSettings() {
    m_menuOpen = true;
    updateReveal();
    emit settingsRequested();
}

void DockController::closeMenu() {
    if (!m_menuOpen) return;
    m_menuOpen = false;
    m_hideTimer.start();
    emit menuClosed();
}

void DockController::invokeMenu(int index) { invokePath({index}); }

void DockController::invokePath(const QVariantList &path) {
    const QVector<DockMenuRow> *level = &m_menu;
    const DockMenuRow *r = nullptr;
    for (const QVariant &p : path) {
        const int i = p.toInt();
        if (i < 0 || i >= level->size()) return;
        r = &(*level)[i];
        level = &r->children;
    }
    if (!r) return;
    const DockMenuRow row = *r;          // closeMenu() and the engine may rebuild the menu
    const int menuRow = m_menuRow;
    closeMenu();
    invokeRow(row, menuRow);
}

void DockController::invokeRow(const DockMenuRow &r, int row) {
    const DockItem *it = row >= 0 && row < m_model.items().size() ? &m_model.items()[row] : nullptr;
    if (!r.enabled || r.kind == DockMenuRow::Separator) return;
    if (!r.children.isEmpty() && !r.id.startsWith(QLatin1String("window:"))) return;     // a submenu opens, it does not run
    if (r.id.startsWith(QLatin1String("window:"))) {
        const int id = r.windowId;
        const WindowInfo *w = m_model.window(id);
        if (w && w->minimised) m_engine->restore(id); else m_engine->focus(id);
    } else if (r.id == QLatin1String("restore")) {
        m_engine->restore(r.windowId);
    } else if (r.id == QLatin1String("close")) {
        m_engine->close(r.windowId);
    } else if (r.id == QLatin1String("new-window") && it) {
        launch(it->desktopId, true);
    } else if (r.id == QLatin1String("pin") && it) {
        m_settings.setPinned(m_model.pinnedWith(it->desktopId));
    } else if (r.id == QLatin1String("unpin") && it) {
        m_settings.setPinned(m_model.pinnedWithout(it->desktopId));
    } else if (r.id.startsWith(QLatin1String("window-"))) {
        windowAction(r.id);
    } else if (r.id == QLatin1String("minimise-all") && it) {
        for (int id : it->windows) if (const WindowInfo *w = m_model.window(id)) if (!w->minimised) m_engine->minimise(id);
    } else if (r.id == QLatin1String("quit") && it) {
        DockAction a;
        a.type = DockAction::CloseAll;
        a.key = it->key;
        perform(a);
    } else if (r.id == QLatin1String("settings")) {
        openSettings();
    }
}

void DockController::movePinned(int from, int to) {
    const QStringList l = m_model.pinnedAfterMove(from, to);
    if (l != m_model.pinnedList()) m_settings.setPinned(l);
}

// ---------------------------------------------------------------------------------------------------- taskbar

void DockController::taskbarStart() {
    m_spawn(qEnvironmentVariable("TAOS_LAUNCHER_CMD", QStringLiteral("taos-launcher")), {QStringLiteral("toggle")});
}

void DockController::taskbarActivate(int id) {
    const int p = m_taskbar.positionOf(id);
    if (p < 0) return;
    const TaskbarButton &b = m_taskbar.buttons()[p];
    if (b.minimised) m_engine->restore(id);
    else if (b.focused) m_engine->minimise(id);
    else m_engine->focus(id);
}

static DockMenuRow windowRow(const QString &id, const QString &text, int win, bool danger = false) {
    DockMenuRow r;
    r.id = id;
    r.text = text;
    r.windowId = win;
    r.danger = danger;
    return r;
}

void DockController::taskbarMenu(int id, qreal x, qreal y) {
    const int p = m_taskbar.positionOf(id);
    if (p < 0) return;
    // The same window controls as the dock's menu (one builder), after a Show for a window that is up.
    showMenu([this, id] {
        QVector<DockMenuRow> rows;
        const int p = m_taskbar.positionOf(id);
        if (p >= 0 && !m_taskbar.buttons()[p].minimised) {
            rows << windowRow(QStringLiteral("window:%1").arg(id), tr("Show"), id);
            DockMenuRow sep;
            sep.kind = DockMenuRow::Separator;
            rows << sep;
        }
        return rows + m_model.windowControlRows(id);
    }, x, y, -1);
}

void DockController::taskbarOverflowMenu(int first, int visible, qreal x, qreal y) {
    m_menu.clear();
    const auto &bs = m_taskbar.buttons();
    for (int i = 0; i < bs.size(); ++i) {
        if (i >= first && i < first + visible) continue;
        DockMenuRow r = windowRow(QStringLiteral("window:%1").arg(bs[i].id), bs[i].title, bs[i].id);
        r.minimised = bs[i].minimised;
        m_menu << r;
    }
    if (m_menu.isEmpty()) return;
    m_menuBuild = nullptr;
    m_menuRow = -1;
    m_menuRows = toVariant(m_menu);
    m_menuOpen = true;
    emit menuRowsChanged();
    emit menuRequested(x, y, -1);
}

QVariantMap DockController::taskbarLayout(int available, int minW, int maxW, int gap, int moreW) const {
    const TaskbarLayout::Result r = TaskbarLayout::compute(available, m_taskbar.rowCount(), m_taskbar.focusedPosition(), minW, maxW, gap, moreW);
    return {{QStringLiteral("buttonWidth"), r.buttonWidth}, {QStringLiteral("first"), r.first},
            {QStringLiteral("visible"), r.visible}, {QStringLiteral("hidden"), r.hidden}};
}
