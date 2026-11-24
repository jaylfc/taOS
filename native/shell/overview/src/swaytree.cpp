// SPDX-License-Identifier: AGPL-3.0-or-later
#include "swaytree.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QUrl>
#include <algorithm>

bool WinInfo::operator==(const WinInfo &o) const {
    return conId == o.conId && appId == o.appId && title == o.title && workspace == o.workspace
           && workspaceNum == o.workspaceNum && output == o.output && focused == o.focused
           && urgent == o.urgent && floating == o.floating && minimised == o.minimised && rect == o.rect
           && identifier == o.identifier;
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
    // A leaf with a pid, an app_id or an X11 window is a window; a bare empty split is not.
    return n.contains(QLatin1String("pid")) || !n.value(QLatin1String("app_id")).isNull()
           || !n.value(QLatin1String("window")).isNull();
}

static WinInfo winFrom(const QJsonObject &n) {
    WinInfo w;
    w.conId = n.value(QLatin1String("id")).toInt();
    QString app = n.value(QLatin1String("app_id")).toString();
    if (app.isEmpty()) {
        const QJsonObject props = n.value(QLatin1String("window_properties")).toObject();
        app = props.value(QLatin1String("class")).toString();
    }
    w.appId = app;
    w.title = n.value(QLatin1String("name")).toString();
    w.focused = n.value(QLatin1String("focused")).toBool();
    w.urgent = n.value(QLatin1String("urgent")).toBool();
    const QString fl = n.value(QLatin1String("floating")).toString();
    w.floating = fl == QLatin1String("user_on") || fl == QLatin1String("auto_on")
                 || n.value(QLatin1String("type")).toString() == QLatin1String("floating_con");
    w.rect = rectOf(n.value(QLatin1String("rect")));
    w.identifier = n.value(QLatin1String("foreign_toplevel_identifier")).toString();
    w.pid = n.value(QLatin1String("pid")).toInt();
    return w;
}

bool SwayTree::parseWindowNode(const QByteArray &json, WinInfo *out) {
    const QJsonDocument d = QJsonDocument::fromJson(json);
    if (!d.isObject()) return false;
    *out = winFrom(d.object());
    return out->conId != 0;
}

// Leaf windows under the scratchpad that carry taos_min_<id>: minimised windows. Anything else in the
// scratchpad is somebody else's and is not listed.
static void collectParked(const QJsonObject &n, QVector<QJsonObject> *out) {
    if (isLeafWindow(n)) {
        const QString mark = QStringLiteral("taos_min_%1").arg(n.value(QLatin1String("id")).toInt());
        for (const QJsonValue &m : n.value(QLatin1String("marks")).toArray())
            if (m.toString() == mark) { out->push_back(n); return; }
        return;
    }
    for (const QJsonValue &v : n.value(QLatin1String("nodes")).toArray()) collectParked(v.toObject(), out);
    for (const QJsonValue &v : n.value(QLatin1String("floating_nodes")).toArray()) collectParked(v.toObject(), out);
}

static void walk(const QJsonObject &n, const QString &output, const QString &ws, int wsNum, Snapshot *s,
                 QVector<int> *order) {
    if (isLeafWindow(n)) {
        WinInfo w = winFrom(n);
        w.output = output;
        w.workspace = ws;
        w.workspaceNum = wsNum;
        s->windows.push_back(w);
        if (w.focused) s->focusedConId = w.conId;
        order->push_back(w.conId);
        return;
    }
    // sway lists a container's children in `nodes`/`floating_nodes`; `focus` is the focus stack by id.
    QVector<QJsonObject> kids;
    for (const QJsonValue &v : n.value(QLatin1String("nodes")).toArray()) kids.push_back(v.toObject());
    for (const QJsonValue &v : n.value(QLatin1String("floating_nodes")).toArray()) kids.push_back(v.toObject());
    QVector<int> focus;
    for (const QJsonValue &v : n.value(QLatin1String("focus")).toArray()) focus.push_back(v.toInt());
    std::stable_sort(kids.begin(), kids.end(), [&](const QJsonObject &a, const QJsonObject &b) {
        const int ia = focus.indexOf(a.value(QLatin1String("id")).toInt());
        const int ib = focus.indexOf(b.value(QLatin1String("id")).toInt());
        return (ia < 0 ? 1 << 20 : ia) < (ib < 0 ? 1 << 20 : ib);
    });
    for (const QJsonObject &k : kids) walk(k, output, ws, wsNum, s, order);
}

bool SwayTree::parse(const QByteArray &json, Snapshot *out, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (!d.isObject()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not an object");
        return false;
    }
    Snapshot s;
    QVector<QVector<int>> perWs;
    QVector<QJsonObject> parked;
    const QJsonObject root = d.object();
    for (const QJsonValue &ov : root.value(QLatin1String("nodes")).toArray()) {
        const QJsonObject o = ov.toObject();
        const QString oname = o.value(QLatin1String("name")).toString();
        if (oname.startsWith(QLatin1String("__"))) {  // __i3, the scratchpad holder: windows taos-window parked
            for (const QJsonValue &sv : o.value(QLatin1String("nodes")).toArray())
                collectParked(sv.toObject(), &parked);
            continue;
        }
        OutInfo oi;
        oi.name = oname;
        oi.rect = rectOf(o.value(QLatin1String("rect")));
        oi.currentWorkspace = o.value(QLatin1String("current_workspace")).toString();
        oi.focused = o.value(QLatin1String("focused")).toBool();
        s.outputs.push_back(oi);
        for (const QJsonValue &wv : o.value(QLatin1String("nodes")).toArray()) {
            const QJsonObject w = wv.toObject();
            if (w.value(QLatin1String("type")).toString() != QLatin1String("workspace")) continue;
            const QString wname = w.value(QLatin1String("name")).toString();
            if (wname == QLatin1String("__i3_scratch")) continue;
            WsInfo wi;
            wi.id = w.value(QLatin1String("id")).toInt();
            wi.name = wname;
            wi.num = w.contains(QLatin1String("num")) ? w.value(QLatin1String("num")).toInt(-1) : -1;
            if (wi.num < 0) {
                bool ok = false;
                const int n = wname.section(QLatin1Char(':'), 0, 0).toInt(&ok);
                wi.num = ok ? n : -1;
            }
            wi.output = oname;
            wi.urgent = w.value(QLatin1String("urgent")).toBool();
            wi.rect = rectOf(w.value(QLatin1String("rect")));
            wi.visible = wname == oi.currentWorkspace;
            const int before = s.windows.size();
            QVector<int> order;
            walk(w, oname, wname, wi.num, &s, &order);
            bool focusedHere = false;
            for (int i = before; i < s.windows.size(); ++i) focusedHere |= s.windows[i].focused;
            // The workspace node is "focused" when it holds focus but no window does (an empty workspace).
            wi.focused = focusedHere || w.value(QLatin1String("focused")).toBool();
            if (wi.focused) {
                s.focusedWorkspace = wname;
                s.focusedOutput = oname;
            }
            s.workspaces.push_back(wi);
            perWs.push_back(order);
        }
    }
    for (OutInfo &o : s.outputs) o.focused = (o.name == s.focusedOutput);
    for (const QJsonObject &n : std::as_const(parked)) {
        WinInfo w = winFrom(n);
        w.minimised = true;
        for (const QJsonValue &m : n.value(QLatin1String("marks")).toArray()) {
            const QString ms = m.toString();
            if (ms.startsWith(QLatin1String("taos_ws_"))) w.workspace = QUrl::fromPercentEncoding(ms.mid(8).toUtf8());
            else if (ms == QLatin1String("taos_mfl")) w.floating = true;
        }
        if (n.value(QLatin1String("type")).toString() == QLatin1String("floating_con") && !w.floating) w.floating = false;
        for (const WsInfo &ws : std::as_const(s.workspaces))
            if (ws.name == w.workspace) { w.output = ws.output; w.workspaceNum = ws.num; break; }
        if (w.output.isEmpty() && !s.outputs.isEmpty()) w.output = s.focusedOutput.isEmpty() ? s.outputs.first().name : s.focusedOutput;
        s.windows.push_back(w);   // after the live ones; the MRU tracker orders them
    }
    // Seed: focused window, then focused workspace's stack, then the rest in tree order.
    QVector<int> seed;
    if (s.focusedConId) seed.push_back(s.focusedConId);
    for (int pass = 0; pass < 2; ++pass)
        for (int i = 0; i < s.workspaces.size(); ++i) {
            const bool cur = s.workspaces[i].name == s.focusedWorkspace;
            if ((pass == 0) != cur) continue;
            for (int id : perWs[i]) if (!seed.contains(id)) seed.push_back(id);
        }
    s.seedOrder = seed;
    *out = s;
    return true;
}
