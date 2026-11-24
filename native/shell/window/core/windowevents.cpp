// SPDX-License-Identifier: AGPL-3.0-or-later
#include "windowevents.h"

#include <QJsonArray>
#include <QDateTime>
#include <QJsonDocument>
#include <QThread>

#include "desktopmodes.h"
#include "swayconn.h"
#include "windowops.h"

namespace TaosWin {

bool followWindows(const QString &socketPath, const std::function<void(const WindowEvent &)> &sink,
                   const std::function<bool()> &stop, QString *error, int pollMs) {
    SwayTransport tx(socketPath);
    SwayConnection ev;
    if (!ev.open(socketPath, error)) return false;
    QByteArray reply;
    const QByteArray subs = QJsonDocument(QJsonArray{QStringLiteral("window"), QStringLiteral("workspace"),
                                                     QStringLiteral("shutdown")}).toJson(QJsonDocument::Compact);
    if (!ev.request(Ipc::Subscribe, subs, &reply, error)) return false;
    Tree prev;
    WindowController c(&tx);
    if (!c.list(&prev, error)) return false;
    DesktopModes modes;
    Modes::read(Modes::path(), &modes);
    // desktop.json is polled with the event loop's timeout: a change made by any process becomes `mode` events.
    auto modeEvents = [&](const Tree &tree) {
        DesktopModes now;
        if (!Modes::read(Modes::path(), &now) || now == modes) return;
        QStringList names;
        for (const Workspace &w : tree.workspaces) names << w.name;
        for (const QString &k : modes.workspaces.keys() + now.workspaces.keys()) if (!names.contains(k)) names << k;
        for (const QString &n : std::as_const(names))
            if (modes.modeFor(n) != now.modeFor(n)) {
                WindowEvent e;
                e.kind = QStringLiteral("mode");
                e.workspace = n;
                e.mode = now.modeFor(n);
                sink(e);
            }
        modes = now;
    };
    while (!stop()) {
        quint32 type = 0;
        QByteArray payload;
        bool closed = false;
        if (!ev.next(&type, &payload, pollMs, &closed)) {
            if (closed) return true;
            modeEvents(prev);
            continue;
        }
        if (type == Ipc::EventShutdown) return true;
        // Take everything that is already waiting, then look once.
        while (ev.next(&type, &payload, 0, &closed)) {
            if (type == Ipc::EventShutdown) return true;
        }
        Tree now;
        QString e;
        if (!c.list(&now, &e)) continue;
        for (const WindowEvent &w : diffWindows(prev, now)) sink(w);
        prev = now;
        modeEvents(prev);
    }
    return true;
}

bool runModeDaemon(const QString &socketPath, const std::function<bool()> &stop, QString *error) {
    SwayTransport tx(socketPath);
    return followWindows(socketPath, [&](const WindowEvent &e) {
        if (e.kind == QLatin1String("minimise") && !e.window.taosMinimised) {
            // Parked by someone else (set_minimized, a panel): add our marks from what we saw a moment ago.
            for (const QString &cmd : Plans::adoptMinimised(e.before, e.window, QDateTime::currentMSecsSinceEpoch()).commands)
                if (!tx.runCommand(cmd, nullptr)) break;
            return;
        }
        if (e.kind == QLatin1String("restore") && !e.window.marks.isEmpty()) {
            for (const QString &cmd : Plans::clearMinimiseMarks(e.window).commands)
                if (!tx.runCommand(cmd, nullptr)) break;
            return;
        }
        if (e.kind != QLatin1String("new")) return;
        WindowController c(&tx);
        Tree t;
        if (!c.list(&t, nullptr)) return;
        const Window *w = t.find(e.window.id);
        DesktopModes modes;
        Modes::read(Modes::path(), &modes);
        if (!w) return;
        const QRect want = newWindowGeometry(t, *w, modes);
        if (!want.isValid()) return;
        for (const QString &cmd : newWindowCommands(t, *w, modes))
            if (!tx.runCommand(cmd, nullptr)) return;
        c.settleGeometry(w->id, want);
    }, stop, error);
}

}  // namespace TaosWin
