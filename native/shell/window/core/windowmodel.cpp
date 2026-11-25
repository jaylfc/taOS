// SPDX-License-Identifier: AGPL-3.0-or-later
#include "windowmodel.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <algorithm>

namespace TaosWin {

namespace Marks {
QString encodeName(const QString &name) {
    QString out;
    for (const QByteArray::value_type b : name.toUtf8()) {
        const char c = char(b);
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '-')
            out += QLatin1Char(c);
        else
            out += QLatin1Char('%') + QString::number(uchar(b), 16).rightJustified(2, QLatin1Char('0'));
    }
    return out;
}
QString decodeName(const QString &enc) {
    QByteArray out;
    const QByteArray in = enc.toLatin1();
    for (int i = 0; i < in.size(); ++i) {
        if (in[i] == '%' && i + 2 < in.size()) {
            bool ok = false;
            const int v = in.mid(i + 1, 2).toInt(&ok, 16);
            if (ok) { out.append(char(v)); i += 2; continue; }
        }
        out.append(in[i]);
    }
    return QString::fromUtf8(out);
}
QString geometry(const QString &prefix, const QRect &r) {
    return QStringLiteral("%1%2_%3_%4_%5").arg(prefix).arg(r.x()).arg(r.y()).arg(r.width()).arg(r.height());
}
bool parseGeometry(const QString &mark, const QString &prefix, QRect *out) {
    if (!mark.startsWith(prefix)) return false;
    const QStringList p = mark.mid(prefix.size()).split(QLatin1Char('_'));
    if (p.size() != 4) return false;
    int v[4];
    for (int i = 0; i < 4; ++i) {
        bool ok = false;
        v[i] = p[i].toInt(&ok);
        if (!ok) return false;
    }
    *out = QRect(v[0], v[1], v[2], v[3]);
    return true;
}
}  // namespace Marks

bool isOurMark(const QString &m) { return m.startsWith(QLatin1String("taos_")); }

QJsonObject Window::toJson() const {
    QJsonObject o;
    o.insert(QStringLiteral("id"), id);
    o.insert(QStringLiteral("app_id"), appId);
    o.insert(QStringLiteral("title"), title);
    o.insert(QStringLiteral("pid"), pid);
    o.insert(QStringLiteral("workspace"), workspace);
    o.insert(QStringLiteral("focused"), focused);
    o.insert(QStringLiteral("minimised"), minimised);
    o.insert(QStringLiteral("floating"), floating);
    o.insert(QStringLiteral("fullscreen"), fullscreen);
    o.insert(QStringLiteral("output"), output);
    o.insert(QStringLiteral("full_size"), fullSize);
    return o;
}

QJsonObject Workspace::toJson() const {
    QJsonObject o;
    o.insert(QStringLiteral("name"), name);
    o.insert(QStringLiteral("output"), output);
    o.insert(QStringLiteral("focused"), focused);
    o.insert(QStringLiteral("visible"), visible);
    o.insert(QStringLiteral("windows"), count);
    return o;
}

bool Window::operator==(const Window &o) const {
    return id == o.id && appId == o.appId && title == o.title && pid == o.pid && workspace == o.workspace
           && focused == o.focused && minimised == o.minimised && floating == o.floating
           && fullscreen == o.fullscreen && output == o.output && fullSize == o.fullSize;
}

QJsonObject WindowEvent::toJson() const {
    QJsonObject o;
    o.insert(QStringLiteral("event"), kind);
    if (kind == QLatin1String("mode")) {
        o.insert(QStringLiteral("workspace"), workspace);
        o.insert(QStringLiteral("mode"), mode);
        return o;
    }
    o.insert(QStringLiteral("window"), window.toJson());
    return o;
}

const Window *Tree::find(int id) const {
    for (const Window &w : windows) if (w.id == id) return &w;
    return nullptr;
}
const Workspace *Tree::workspace(const QString &name) const {
    for (const Workspace &w : workspaces) if (w.name == name) return &w;
    return nullptr;
}
const Window *Tree::lastMinimised() const {
    const Window *best = nullptr;
    for (const Window &w : windows)
        if (w.taosMinimised && (!best || w.minimisedAt >= best->minimisedAt)) best = &w;
    return best;
}

static QRect rectOf(const QJsonValue &v) {
    const QJsonObject o = v.toObject();
    return QRect(o.value(QLatin1String("x")).toInt(), o.value(QLatin1String("y")).toInt(),
                 o.value(QLatin1String("width")).toInt(), o.value(QLatin1String("height")).toInt());
}

static bool isLeafWindow(const QJsonObject &n) {
    const QString type = n.value(QLatin1String("type")).toString();
    if (type != QLatin1String("con") && type != QLatin1String("floating_con")) return false;
    if (!n.value(QLatin1String("nodes")).toArray().isEmpty()) return false;
    if (!n.value(QLatin1String("floating_nodes")).toArray().isEmpty()) return false;
    return n.contains(QLatin1String("pid")) || !n.value(QLatin1String("app_id")).isNull()
           || !n.value(QLatin1String("window")).isNull();
}

static Window windowFrom(const QJsonObject &n) {
    Window w;
    w.id = n.value(QLatin1String("id")).toInt();
    QString app = n.value(QLatin1String("app_id")).toString();
    if (app.isEmpty()) app = n.value(QLatin1String("window_properties")).toObject().value(QLatin1String("class")).toString();
    w.appId = app;
    w.title = n.value(QLatin1String("name")).toString();
    w.pid = n.value(QLatin1String("pid")).toInt();
    w.focused = n.value(QLatin1String("focused")).toBool();
    const QString fl = n.value(QLatin1String("floating")).toString();
    w.floating = fl == QLatin1String("user_on") || fl == QLatin1String("auto_on")
                 || n.value(QLatin1String("type")).toString() == QLatin1String("floating_con");
    w.fullscreen = n.value(QLatin1String("fullscreen_mode")).toInt() != 0;
    w.rect = rectOf(n.value(QLatin1String("rect")));
    // A window with a sway title bar (border normal; taos-frame gives web apps one): sway's `rect` is the content below
    // the bar, but `resize set` and `move position` talk about the whole container, bar included. Keep `rect` in the
    // container's terms so what is saved (taos_geo_, taos_fgeo_) and compared with the workspace is what the commands
    // replay; otherwise every full size or minimise cycle drops the window by one bar and shrinks it by one.
    const int bar = rectOf(n.value(QLatin1String("deco_rect"))).height();
    if (bar > 0 && w.rect.isValid() && !w.fullscreen && n.value(QLatin1String("border")).toString() == QLatin1String("normal"))
        w.rect.setTop(w.rect.top() - bar);
    for (const QJsonValue &m : n.value(QLatin1String("marks")).toArray()) w.marks << m.toString();
    return w;
}

static void applyMarks(Window *w) {
    const QString minMark = QStringLiteral("taos_min_%1").arg(w->id);
    for (const QString &m : std::as_const(w->marks)) {
        if (m == minMark) w->taosMinimised = true;
        else if (m == QLatin1String("taos_full")) w->fullSize = true;
        else if (m == QLatin1String("taos_ftiled")) w->fullWasTiled = true;
        else if (m == QLatin1String("taos_mfl")) w->minWasFloating = true;
        else if (m == QLatin1String("taos_mfs")) w->minWasFullscreen = true;
        else if (m.startsWith(QLatin1String("taos_at_"))) w->minimisedAt = m.mid(8).toLongLong();
        else if (m.startsWith(QLatin1String("taos_ws_"))) w->workspace = Marks::decodeName(m.mid(8));
        else {
            QRect r;
            if (Marks::parseGeometry(m, QStringLiteral("taos_geo_"), &r)) w->savedGeometry = r;
            else if (Marks::parseGeometry(m, QStringLiteral("taos_fgeo_"), &r)) w->fullSavedGeometry = r;
        }
    }
}

namespace {
struct Walk {
    Tree *t;
    QVector<Window> scratch;
    void leaf(const QJsonObject &n, const QString &output, const QString &ws, bool inScratch) {
        Window w = windowFrom(n);
        w.output = output;
        w.workspace = ws;
        if (inScratch) {
            w.inScratchpad = true;
            w.minimised = true;
            applyMarks(&w);
            // A hidden window we did not park (a user scratchpad) has no workspace to return to.
            if (!w.taosMinimised) w.workspace.clear();
            w.floating = w.taosMinimised ? w.minWasFloating : true;
            w.fullscreen = w.taosMinimised && w.minWasFullscreen;
            w.output.clear();
            scratch.push_back(w);
            return;
        }
        applyMarks(&w);
        w.taosMinimised = false;  // a stale taos_min_ mark on a window that is on a workspace again
        if (w.focused) t->focusedId = w.id;
        t->windows.push_back(w);
    }
    void node(const QJsonObject &n, const QString &output, const QString &ws, bool inScratch) {
        if (isLeafWindow(n)) { leaf(n, output, ws, inScratch); return; }
        for (const QJsonValue &v : n.value(QLatin1String("nodes")).toArray()) node(v.toObject(), output, ws, inScratch);
        for (const QJsonValue &v : n.value(QLatin1String("floating_nodes")).toArray()) node(v.toObject(), output, ws, inScratch);
    }
};
}  // namespace

bool parseTree(const QByteArray &json, Tree *out, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (!d.isObject()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not an object");
        return false;
    }
    *out = Tree();
    Walk w{out, {}};
    for (const QJsonValue &ov : d.object().value(QLatin1String("nodes")).toArray()) {
        const QJsonObject o = ov.toObject();
        const QString oname = o.value(QLatin1String("name")).toString();
        const bool scratchOutput = oname == QLatin1String("__i3");
        for (const QJsonValue &wv : o.value(QLatin1String("nodes")).toArray()) {
            const QJsonObject ws = wv.toObject();
            if (ws.value(QLatin1String("type")).toString() != QLatin1String("workspace")) continue;
            const QString wname = ws.value(QLatin1String("name")).toString();
            const bool scratch = scratchOutput || wname == QLatin1String("__i3_scratch");
            if (!scratch) {
                Workspace wi;
                wi.name = wname;
                wi.output = oname;
                wi.rect = rectOf(ws.value(QLatin1String("rect")));
                wi.focused = ws.value(QLatin1String("focused")).toBool();
                wi.visible = ws.value(QLatin1String("visible")).toBool();
                out->workspaces.push_back(wi);
            }
            w.node(ws, scratch ? QString() : oname, scratch ? QString() : wname, scratch);
        }
    }
    for (const Window &s : std::as_const(w.scratch)) out->windows.push_back(s);
    // "focused workspace": the one holding the focused window, else the visible one of the focused output.
    for (const Workspace &ws : std::as_const(out->workspaces)) {
        if (ws.focused) out->focusedWorkspace = ws.name;
    }
    if (out->focusedWorkspace.isEmpty()) {
        const Window *f = out->find(out->focusedId);
        if (f) out->focusedWorkspace = f->workspace;
    }
    for (const Window &win : std::as_const(out->windows)) {
        if (win.inScratchpad) continue;
        for (Workspace &ws : out->workspaces)
            if (ws.name == win.workspace && ws.output == win.output) { ++ws.count; break; }
    }
    // A parked window's output: the output of its workspace, when that workspace still exists.
    for (Window &win : out->windows) {
        if (win.inScratchpad && !win.workspace.isEmpty()) {
            const Workspace *ws = out->workspace(win.workspace);
            win.output = ws ? ws->output : QString();
        }
    }
    return true;
}

QList<WindowEvent> diffWindows(const Tree &before, const Tree &after) {
    QList<WindowEvent> ev;
    for (const Window &b : before.windows) {
        if (!after.find(b.id)) ev.push_back({QStringLiteral("close"), b});
    }
    for (const Window &a : after.windows) {
        const Window *b = before.find(a.id);
        if (!b) { ev.push_back({QStringLiteral("new"), a}); continue; }
        if (!b->minimised && a.minimised) ev.push_back({QStringLiteral("minimise"), a, *b});
        else if (b->minimised && !a.minimised) ev.push_back({QStringLiteral("restore"), a, *b});
        if (b->title != a.title) ev.push_back({QStringLiteral("title"), a});
        if (!b->focused && a.focused) ev.push_back({QStringLiteral("focus"), a});
        // Where it is or how big it is meant to be changed (maximise, full screen, floating, another space): the dock's labels follow.
        if (!b->minimised && !a.minimised
            && (b->fullscreen != a.fullscreen || b->fullSize != a.fullSize || b->floating != a.floating
                || b->workspace != a.workspace || b->output != a.output))
            ev.push_back({QStringLiteral("state"), a});
    }
    return ev;
}

const Window *findOwn(const Tree &t, int pid, const QString &appId) {
    const Window *byApp = nullptr;
    for (const Window &w : t.windows) {
        if (pid > 0 && w.pid == pid) return &w;
        if (!appId.isEmpty() && w.appId == appId && (!byApp || w.focused)) byApp = &w;
    }
    return byApp;
}

}  // namespace TaosWin
