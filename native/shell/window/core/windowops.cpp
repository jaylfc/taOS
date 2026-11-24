#include "windowops.h"

#include <QDateTime>
#include <QThread>

#include "swayconn.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

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

Plan fullscreen(const Window &w) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    p.commands << sel(w.id) + QStringLiteral("fullscreen toggle");
    return p;
}

Plan full(const Window &w, const Tree &t) {
    Plan p;
    if (w.minimised) { p.error = QStringLiteral("the window is minimised; restore it first"); return p; }
    if (w.fullscreen) { p.commands << sel(w.id) + QStringLiteral("fullscreen disable"); return p; }
    if (w.fullSize) {
        // Back to what it was.
        if (w.fullWasTiled) {
            p.commands << sel(w.id) + QStringLiteral("floating disable");
        } else if (w.fullSavedGeometry.isValid()) {
            const QRect r = w.fullSavedGeometry;
            p.commands << sel(w.id) + QStringLiteral("resize set %1 %2").arg(r.width()).arg(r.height());
            p.commands << sel(w.id) + QStringLiteral("move position %1 %2").arg(r.x()).arg(r.y());
        }
        unmarkAll(&p, w, isFullMark);
        return p;
    }
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) { p.error = QStringLiteral("cannot find the workspace of the window"); return p; }
    const QRect r = ws->rect;
    if (!w.floating) addMark(&p, w, QStringLiteral("taos_ftiled"));
    else addMark(&p, w, Marks::geometry(QStringLiteral("taos_fgeo_"), w.rect));
    addMark(&p, w, QStringLiteral("taos_full"));
    if (!w.floating) p.commands << sel(w.id) + QStringLiteral("floating enable");
    p.commands << sel(w.id) + QStringLiteral("resize set %1 %2").arg(r.width()).arg(r.height());
    p.commands << sel(w.id) + QStringLiteral("move position %1 %2").arg(r.x()).arg(r.y());
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

bool WindowController::fullscreen(const QString &ref, QString *error) {
    Tree t; const Window *w = nullptr;
    if (!resolve(ref, &t, &w, error, false)) return false;
    return run(Plans::fullscreen(*w), error);
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
    // Entering full size: read the geometry back and correct it. A client may answer the first
    // configure after the resize (or sway counts borders), so look a few times.
    if (!w->fullSize && !w->fullscreen && p.ok() && !p.commands.isEmpty()) {
        const Workspace *ws = t.workspace(w->workspace);
        for (int attempt = 0; ws && attempt < 8; ++attempt) {
            QThread::msleep(attempt == 0 ? 20 : 60);
            Tree after;
            if (!list(&after, nullptr)) break;
            const Window *a = after.find(w->id);
            if (!a || a->rect == ws->rect) break;
            const QRect r = ws->rect, have = a->rect;
            const int dw = r.width() - have.width(), dh = r.height() - have.height();
            const bool near = qAbs(dw) <= 6 && qAbs(dh) <= 6;   // borders: ask for the difference too
            const QSize want = near ? QSize(r.width() + dw, r.height() + dh) : r.size();
            m_t->runCommand(Plans::sel(w->id) + QStringLiteral("resize set %1 %2").arg(want.width()).arg(want.height()), nullptr);
            m_t->runCommand(Plans::sel(w->id) + QStringLiteral("move position %1 %2").arg(r.x()).arg(r.y()), nullptr);
        }
    }
    return true;
}

bool WindowController::settleGeometry(int id, const QRect &want) {
    for (int attempt = 0; attempt < 10; ++attempt) {
        QThread::msleep(attempt == 0 ? 30 : 70);
        Tree after;
        if (!list(&after, nullptr)) return false;
        const Window *a = after.find(id);
        if (!a || !a->floating || a->minimised) return false;
        if (a->rect == want) return true;
        m_t->runCommand(Plans::sel(id) + QStringLiteral("resize set %1 %2").arg(want.width()).arg(want.height()), nullptr);
        m_t->runCommand(Plans::sel(id) + QStringLiteral("move position %1 %2").arg(want.x()).arg(want.y()), nullptr);
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
