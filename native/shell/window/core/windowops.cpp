// SPDX-License-Identifier: AGPL-3.0-or-later
#include "windowops.h"

#include <QDateTime>
#include <QThread>

#include "desktopmodes.h"
#include "swayconn.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

namespace TaosWin {

struct SwayTransport::Impl {
    SwayConnection conn;
};

SwayTransport::~SwayTransport() { delete m_impl; }

bool SwayTransport::connectNow(QString *error) {
    if (!m_impl) m_impl = new Impl;
    if (m_impl->conn.isOpen()) return true;
    return m_impl->conn.open(m_path, error);
}

bool SwayTransport::getTree(QByteArray *json, QString *error) {
    if (!connectNow(error)) return false;
    if (m_impl->conn.request(Ipc::GetTree, QByteArray(), json, error)) return true;
    m_impl->conn.close();
    return false;
}

bool SwayTransport::runCommand(const QString &cmd, QString *error) {
    if (!connectNow(error)) return false;
    QByteArray reply;
    if (!m_impl->conn.request(Ipc::RunCommand, cmd.toUtf8(), &reply, error)) {
        m_impl->conn.close();
        return false;
    }
    const QJsonDocument d = QJsonDocument::fromJson(reply);
    if (d.isArray()) {
        for (const QJsonValue &v : d.array()) {
            const QJsonObject o = v.toObject();
            if (!o.value(QLatin1String("success")).toBool(true)) {
                if (error) *error = o.value(QLatin1String("error")).toString(QStringLiteral("sway refused the command"));
                return false;
            }
        }
    }
    return true;
}

namespace Plans {

QString quoted(const QString &s) {
    QString o = QStringLiteral("\"");
    for (const QChar c : s) {
        if (c == QLatin1Char('"') || c == QLatin1Char('\\')) o += QLatin1Char('\\');
        o += c;
    }
    return o + QLatin1Char('"');
}
QString sel(int id) { return QStringLiteral("[con_id=%1] ").arg(id); }

static bool isMinMark(const QString &m) {
    return m.startsWith(QLatin1String("taos_min_")) || m.startsWith(QLatin1String("taos_at_"))
           || m.startsWith(QLatin1String("taos_ws_")) || m.startsWith(QLatin1String("taos_geo_"))
           || m == QLatin1String("taos_mfl") || m == QLatin1String("taos_mfs");
}
static bool isFullMark(const QString &m) {
    return m == QLatin1String("taos_full") || m == QLatin1String("taos_ftiled") || m.startsWith(QLatin1String("taos_fgeo_"));
}
static void unmarkAll(Plan *p, const Window &w, bool (*pred)(const QString &)) {
    for (const QString &m : w.marks) if (pred(m)) p->commands << sel(w.id) + QStringLiteral("unmark ") + quoted(m);
}
static void addMark(Plan *p, const Window &w, const QString &mark) {
    p->commands << sel(w.id) + QStringLiteral("mark --add ") + quoted(mark);
}

static void minimiseMarks(Plan *p, const Window &w, qint64 nowMs) {
    addMark(p, w, QStringLiteral("taos_min_%1").arg(w.id));
    addMark(p, w, QStringLiteral("taos_at_%1").arg(nowMs));
    if (!w.workspace.isEmpty()) addMark(p, w, QStringLiteral("taos_ws_") + Marks::encodeName(w.workspace));
    if (w.floating) {
        addMark(p, w, QStringLiteral("taos_mfl"));
        if (!w.fullscreen && w.rect.isValid()) addMark(p, w, Marks::geometry(QStringLiteral("taos_geo_"), w.rect));
    }
    if (w.fullscreen) addMark(p, w, QStringLiteral("taos_mfs"));
}

Plan adoptMinimised(const Window &before, const Window &now, qint64 nowMs) {
    Plan p;
    if (!now.minimised || now.taosMinimised || before.minimised || before.workspace.isEmpty()) return p;
    Window w = before;
    w.id = now.id;
    unmarkAll(&p, now, isMinMark);
    minimiseMarks(&p, w, nowMs);
    return p;
}

Plan clearMinimiseMarks(const Window &w) {
    Plan p;
    unmarkAll(&p, w, isMinMark);
    return p;
}

Plan minimise(const Window &w, qint64 nowMs) {
    Plan p;
    if (w.minimised) return p;   // already: nothing to do
    unmarkAll(&p, w, isMinMark);   // stale marks from an earlier session
    if (w.fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen disable");
    minimiseMarks(&p, w, nowMs);
    p.commands << sel(w.id) + QStringLiteral("move scratchpad");
    return p;
}

Plan restore(const Window &w, const Tree &t) {
    Plan p;
    if (!w.minimised) return p;
    const QString ws = w.workspace;
    if (!ws.isEmpty() && ws != t.focusedWorkspace) p.commands << QStringLiteral("workspace ") + quoted(ws);
    p.commands << sel(w.id) + QStringLiteral("scratchpad show");
    if (w.taosMinimised && !w.minWasFloating) p.commands << sel(w.id) + QStringLiteral("floating disable");
    if (w.taosMinimised && w.minWasFullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen enable");
    unmarkAll(&p, w, isMinMark);
    p.commands << sel(w.id) + QStringLiteral("focus");
    return p;
}

Plan fullscreen(const Window &w, Fullscreen mode) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    // The container is never resized: sway makes it cover the output and tells the client (xdg_toplevel set_fullscreen).
    if (mode == Fullscreen::On) { if (!w.fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen enable"); }
    else if (mode == Fullscreen::Off) { if (w.fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen disable"); }
    else p.commands << sel(w.id) + QStringLiteral("fullscreen toggle");
    return p;
}
// The usable area a window of workspace `ws` can fill. A workspace that is not shown has no reserved space of its own
// worth trusting: take the one shown on the same output.
static QRect areaOf(const Tree &t, const Workspace *ws) {
    if (!ws) return QRect();
    if (ws->visible) return ws->rect;
    for (const Workspace &o : t.workspaces) if (o.visible && o.output == ws->output && o.rect.isValid()) return o.rect;
    return ws->rect;
}

// Resize and move a floating window. Sway puts a floating window that is moved by position on the workspace SHOWN on the
// output under it, so a window of a hidden workspace (`hidden` = its name) is sent back there afterwards, and is not
// settled by the controller (the settling would pull it out again).
static void placeAt(Plan *p, const Window &w, const QRect &g, const QString &hidden = QString()) {
    p->commands << sel(w.id) + QStringLiteral("resize set %1 %2").arg(g.width()).arg(g.height());
    p->commands << sel(w.id) + QStringLiteral("move absolute position %1 %2").arg(g.x()).arg(g.y());
    if (!hidden.isEmpty()) p->commands << sel(w.id) + QStringLiteral("move container to workspace ") + quoted(hidden);
    p->settleId = w.id;
    p->settle = g;
    p->settleHidden = hidden;
}
static QString hiddenName(const Workspace *ws) { return ws && !ws->visible ? ws->name : QString(); }

// The way back from full size (shared by `full` and `unmaximise`).
static void leaveFull(Plan *p, const Window &w, const QString &hidden = QString()) {
    if (w.fullWasTiled) {
        p->commands << sel(w.id) + QStringLiteral("floating disable");
    } else if (w.fullSavedGeometry.isValid()) {
        placeAt(p, w, w.fullSavedGeometry, hidden);
    }
    unmarkAll(p, w, isFullMark);
}

Plan maximise(const Window &w, const Tree &t) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) { p.error = QStringLiteral("cannot find the workspace of the window"); return p; }
    if (w.fullscreen) {
        p.commands << sel(w.id) + QStringLiteral("fullscreen disable");
        // Without full size on record the geometry to go back to is only known once sway has left full screen:
        // the controller plans again after this.
        if (!w.fullSize) return p;
    }
    if (!w.fullSize) {
        if (!w.floating) addMark(&p, w, QStringLiteral("taos_ftiled"));
        else addMark(&p, w, Marks::geometry(QStringLiteral("taos_fgeo_"), w.rect));
        addMark(&p, w, QStringLiteral("taos_full"));
        if (!w.floating) p.commands << sel(w.id) + QStringLiteral("floating enable");
    }
    placeAt(&p, w, areaOf(t, ws), hiddenName(ws));
    return p;
}

Plan unmaximise(const Window &w, const Tree &t) {
    Plan p;
    if (w.minimised) return p;
    if (w.fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen disable");
    if (w.fullSize) leaveFull(&p, w, hiddenName(t.workspace(w.workspace)));
    return p;
}

// Where a floating window goes when it lands on `area`: full size fills it, anything else keeps a sensible size and is centred.
static QRect landing(const Window &w, const QRect &area, const QSize &size) {
    return w.fullSize ? area : centredIn(area, size);
}
QRect landingRect(const Window &w, const QRect &area) { return landing(w, area, w.rect.size()); }

Plan bringHere(const Window &w, const Tree &t) {
    Plan p;
    const Workspace *ws = t.workspace(t.focusedWorkspace);
    if (!ws || !ws->rect.isValid()) { p.error = QStringLiteral("no workspace is focused"); return p; }
    bool floating = w.floating;
    bool fullscreen = w.fullscreen;
    QSize size = w.rect.size();
    if (w.minimised) {
        if (w.savedGeometry.isValid()) size = w.savedGeometry.size();
        p.commands << sel(w.id) + QStringLiteral("scratchpad show");   // it comes up on the focused workspace
        if (w.taosMinimised) {
            floating = w.minWasFloating;
            fullscreen = w.minWasFullscreen;
            if (!floating) p.commands << sel(w.id) + QStringLiteral("floating disable");
            if (fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen enable");
        } else {
            floating = true;
            fullscreen = false;
        }
        unmarkAll(&p, w, isMinMark);
    } else if (w.workspace != ws->name) {
        p.commands << sel(w.id) + QStringLiteral("move container to workspace ") + quoted(ws->name);
    }
    if (floating && !fullscreen) placeAt(&p, w, landing(w, ws->rect, size));
    p.commands << sel(w.id) + QStringLiteral("focus");
    return p;
}

Plan resetSize(const Window &w, const Tree &t) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    if (!w.floating && !w.fullscreen) {
        p.error = QStringLiteral("a tiled window is sized by the layout; switch this workspace to desktop mode to size windows by hand");
        return p;
    }
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) { p.error = QStringLiteral("cannot find the workspace of the window"); return p; }
    if (w.fullscreen) p.commands << sel(w.id) + QStringLiteral("fullscreen disable");
    unmarkAll(&p, w, isFullMark);
    const QRect area = areaOf(t, ws);
    placeAt(&p, w, centredIn(area, defaultWindowSize(area)), hiddenName(ws));
    return p;
}

Plan moveToSpace(const Window &w, const Tree &t, const QString &spaceIn) {
    Plan p;
    const QString space = spaceIn.isEmpty() ? t.focusedWorkspace : spaceIn;
    if (space.isEmpty()) { p.error = QStringLiteral("no workspace is focused"); return p; }
    if (w.minimised) {
        // Parked in the scratchpad: it stays there, and will come back to the new space.
        if (!w.taosMinimised) { p.error = QStringLiteral("the window is hidden in the scratchpad; restore it first"); return p; }
        if (w.workspace == space) return p;
        for (const QString &m : w.marks) if (m.startsWith(QLatin1String("taos_ws_"))) p.commands << sel(w.id) + QStringLiteral("unmark ") + quoted(m);
        addMark(&p, w, QStringLiteral("taos_ws_") + Marks::encodeName(space));
        return p;
    }
    if (w.workspace == space) return p;
    // A workspace that does not exist yet opens on the focused output: its usable area is that one's.
    const Workspace *target = t.workspace(space);
    const Workspace *areaWs = target ? target : t.workspace(t.focusedWorkspace);
    const QRect area = areaOf(t, areaWs);
    const QString moveCmd = sel(w.id) + QStringLiteral("move container to workspace ") + quoted(space);
    if (!w.floating || w.fullscreen || !area.isValid()) { p.commands << moveCmd; return p; }
    const QRect g = landing(w, area, w.rect.size());
    if (target && target->visible) {
        p.commands << moveCmd;
        placeAt(&p, w, g);
    } else {
        // Position first (it takes the window to the shown workspace of the output), then on to the hidden one.
        placeAt(&p, w, g, space);
    }
    return p;
}

QString newSpaceName(const Tree &t) {
    static const QRegularExpression lead(QStringLiteral("^(\\d+)"));
    int top = 0;
    for (const Workspace &ws : t.workspaces) {
        const QRegularExpressionMatch m = lead.match(ws.name);
        if (m.hasMatch()) top = std::max(top, m.captured(1).toInt());
    }
    return QString::number(top + 1);
}

Plan full(const Window &w, const Tree &t) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    if (w.fullscreen) { p.commands << sel(w.id) + QStringLiteral("fullscreen disable"); return p; }
    if (w.fullSize) {   // back to what it was
        leaveFull(&p, w, hiddenName(t.workspace(w.workspace)));
        return p;
    }
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) { p.error = QStringLiteral("cannot find the workspace of the window"); return p; }
    const QRect r = areaOf(t, ws);
    if (!w.floating) addMark(&p, w, QStringLiteral("taos_ftiled"));
    else addMark(&p, w, Marks::geometry(QStringLiteral("taos_fgeo_"), w.rect));
    addMark(&p, w, QStringLiteral("taos_full"));
    if (!w.floating) p.commands << sel(w.id) + QStringLiteral("floating enable");
    placeAt(&p, w, r, hiddenName(ws));
    return p;
}

Plan close(const Window &w) {
    Plan p;
    p.commands << sel(w.id) + QStringLiteral("kill");
    return p;
}

Plan focus(const Window &w, const Tree &t) {
    if (w.minimised) return restore(w, t);
    Plan p;
    p.commands << sel(w.id) + QStringLiteral("focus");
    return p;
}

}  // namespace Plans

bool WindowController::list(Tree *out, QString *error) {
    QByteArray json;
    if (!m_t->getTree(&json, error)) return false;
    return parseTree(json, out, error);
}

bool WindowController::resolve(const QString &ref, Tree *tree, const Window **w, QString *error, bool allowLast) {
    if (!list(tree, error)) return false;
    if (ref == QLatin1String("focused")) {
        *w = tree->find(tree->focusedId);
        if (!*w) { if (error) *error = QStringLiteral("no window has focus"); return false; }
        return true;
    }
    if (ref == QLatin1String("last") && allowLast) {
        *w = tree->lastMinimised();
        if (!*w) { if (error) *error = QStringLiteral("no window is minimised"); return false; }
        return true;
    }
    bool ok = false;
    const int id = ref.toInt(&ok);
    *w = ok ? tree->find(id) : nullptr;
    if (!*w) {
        if (error) *error = ok ? QStringLiteral("no window with id %1").arg(id)
                               : QStringLiteral("'%1' is not a window id (use a number, \"focused\" or \"last\")").arg(ref);
        return false;
    }
    return true;
}

bool WindowController::run(const Plan &p, QString *error) {
    if (!p.ok()) { if (error) *error = p.error; return false; }
    for (const QString &c : p.commands)
        if (!m_t->runCommand(c, error)) return false;
    return true;
}

bool WindowController::minimise(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    const qint64 now = m_now ? m_now() : QDateTime::currentMSecsSinceEpoch();
    return run(Plans::minimise(*w, now), error);
}

bool WindowController::restore(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, true)) return false;
    if (!w->minimised) {
        // On screen: leave full screen, then full size, back to the geometry before.
        const Plan p = Plans::unmaximise(*w, t);
        if (!run(p, error)) return false;
        if (p.settle.isValid()) settleGeometry(p.settleId, p.settle, p.settleHidden);
        return true;
    }
    return run(Plans::restore(*w, t), error);
}

bool WindowController::toggleMinimise(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    if (w->minimised) return run(Plans::restore(*w, t), error);
    const qint64 now = m_now ? m_now() : QDateTime::currentMSecsSinceEpoch();
    return run(Plans::minimise(*w, now), error);
}

bool WindowController::focus(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, true)) return false;
    return run(Plans::focus(*w, t), error);
}

bool WindowController::fullscreen(const QString &ref, QString *error, Plans::Fullscreen mode) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    return run(Plans::fullscreen(*w, mode), error);
}

bool WindowController::maximise(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    Plan p = Plans::maximise(*w, t);
    if (!run(p, error)) return false;
    if (w->fullscreen && !w->fullSize) {
        // Sway has left full screen and puts the window back where it was (once its transaction commits, which waits for
        // the client): plan again from there, when two looks agree.
        const int id = w->id;
        Tree t2; const Window *w2 = nullptr;
        QRect last;
        const QRect fullscreenRect = w->rect;   // what the tree shows until sway's transaction has committed
        for (int attempt = 0; attempt < 24; ++attempt) {
            QThread::msleep(attempt == 0 ? 30 : 60);
            if (!resolve(QString::number(id), &t2, &w2, error, false)) return false;
            if (!w2->fullscreen && w2->rect != fullscreenRect && w2->rect == last) break;
            last = w2->fullscreen ? QRect() : w2->rect;
        }
        if (w2->fullscreen) { if (error) *error = QStringLiteral("the window did not leave full screen"); return false; }
        p = Plans::maximise(*w2, t2);
        if (!run(p, error)) return false;
    }
    if (p.settle.isValid()) settleFull(p.settleId, p.settle, p.settleHidden);
    return true;
}

bool WindowController::bringHere(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    const Plan p = Plans::bringHere(*w, t);
    if (!run(p, error)) return false;
    if (p.settle.isValid()) settleGeometry(p.settleId, p.settle, p.settleHidden);
    return true;
}

bool WindowController::resetSize(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    const Plan p = Plans::resetSize(*w, t);
    if (!run(p, error)) return false;
    if (p.settle.isValid()) settleGeometry(p.settleId, p.settle, p.settleHidden);
    return true;
}

bool WindowController::moveToSpace(const QString &ref, const QString &space, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    const Plan p = Plans::moveToSpace(*w, t, space);
    if (!run(p, error)) return false;
    if (p.settle.isValid()) settleGeometry(p.settleId, p.settle, p.settleHidden);
    return true;
}

bool WindowController::newSpace(const QString &ref, QString *created, QString *error) {
    Tree t; const Window *w = nullptr;
    QString name;
    if (ref.isEmpty()) {
        if (!list(&t, error)) return false;
        name = Plans::newSpaceName(t);
        if (!m_t->runCommand(QStringLiteral("workspace ") + Plans::quoted(name), error)) return false;
    } else {
        if (!resolve(ref, &t, &w, error, false)) return false;
        if (w->minimised) { if (error) *error = QStringLiteral("the window is minimised; restore it first"); return false; }
        name = Plans::newSpaceName(t);
        const Plan p = Plans::moveToSpace(*w, t, name);
        if (!run(p, error)) return false;
        if (!m_t->runCommand(QStringLiteral("workspace ") + Plans::quoted(name), error)) return false;
        // The new space is shown now: make sure the window is where it should be on it.
        if (w->floating && !w->fullscreen) {
            Tree after;
            const Workspace *ws = list(&after, nullptr) ? after.workspace(name) : nullptr;
            const Window *a = after.find(w->id);
            if (ws && a) settleGeometry(w->id, Plans::landingRect(*a, ws->rect));
        }
        m_t->runCommand(Plans::sel(w->id) + QStringLiteral("focus"), nullptr);
    }
    if (created) *created = name;
    return true;
}

bool WindowController::close(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    return run(Plans::close(*w), error);
}

bool WindowController::full(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    const Plan p = Plans::full(*w, t);
    if (!run(p, error)) return false;
    if (!w->fullSize && !w->fullscreen && p.ok() && !p.commands.isEmpty()) settleFull(w->id, p.settle, p.settleHidden);
    return true;
}

void WindowController::settleFull(int id, const QRect &r, const QString &hidden) {
    // Entering full size: read the geometry back and correct it. A client may answer the first
    // configure after the resize (or sway counts borders), so look a few times.
    for (int attempt = 0; r.isValid() && attempt < 8; ++attempt) {
        QThread::msleep(attempt == 0 ? 20 : 60);
        Tree after;
        if (!list(&after, nullptr)) break;
        const Window *a = after.find(id);
        if (!a || a->rect == r) break;
        const QRect have = a->rect;
        const int dw = r.width() - have.width(), dh = r.height() - have.height();
        const bool near = qAbs(dw) <= 6 && qAbs(dh) <= 6;   // borders: ask for the difference too
        const QSize want = near ? QSize(r.width() + dw, r.height() + dh) : r.size();
        m_t->runCommand(Plans::sel(id) + QStringLiteral("resize set %1 %2").arg(want.width()).arg(want.height()), nullptr);
        m_t->runCommand(Plans::sel(id) + QStringLiteral("move absolute position %1 %2").arg(r.x()).arg(r.y()), nullptr);
        if (!hidden.isEmpty()) m_t->runCommand(Plans::sel(id) + QStringLiteral("move container to workspace ") + Plans::quoted(hidden), nullptr);
    }
}

bool WindowController::settleGeometry(int id, const QRect &want, const QString &hidden) {
    for (int attempt = 0; attempt < 10; ++attempt) {
        QThread::msleep(attempt == 0 ? 30 : 70);
        Tree after;
        if (!list(&after, nullptr)) return false;
        const Window *a = after.find(id);
        if (!a || !a->floating || a->minimised) return false;
        if (a->rect == want) return true;
        m_t->runCommand(Plans::sel(id) + QStringLiteral("resize set %1 %2").arg(want.width()).arg(want.height()), nullptr);
        m_t->runCommand(Plans::sel(id) + QStringLiteral("move absolute position %1 %2").arg(want.x()).arg(want.y()), nullptr);
        if (!hidden.isEmpty()) m_t->runCommand(Plans::sel(id) + QStringLiteral("move container to workspace ") + Plans::quoted(hidden), nullptr);
    }
    return false;
}

int WindowController::ownWindowId(int pid, const QString &appId, QString *error) {
    Tree t;
    if (!list(&t, error)) return 0;
    const Window *w = findOwn(t, pid, appId);
    if (!w && error) *error = QStringLiteral("this application's window is not in the compositor's tree");
    return w ? w->id : 0;
}

}  // namespace TaosWin
