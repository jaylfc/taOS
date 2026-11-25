// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-window: the window engine on the command line. See ../README.md.
#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTextStream>
#include <csignal>
#include <cstdio>

#include "core/desktopmodes.h"
#include "core/windowevents.h"
#include "core/windowops.h"

using namespace TaosWin;

static volatile std::sig_atomic_t g_stop = 0;
static void onSignal(int) { g_stop = 1; }

static int usage(QTextStream &err) {
    err << "usage: taos-window list [--json]\n"
           "       taos-window close|minimise|restore|toggle-minimise|focus|full|maximise|bring-here|reset-size <id|focused|last>\n"
           "       taos-window fullscreen <id|focused> [on|off|toggle]\n"
           "       taos-window move-to-space <id|focused> [space]      (no space: the focused one; a new name makes the space)\n"
           "       taos-window new-space [<id|focused>]                (prints the new space; with a window, it moves there)\n"
           "       taos-window spaces [--json]\n"
           "       taos-window subscribe\n"
           "       taos-window mode get|tiling|desktop|toggle [--workspace <name>]\n"
           "       taos-window mode list [--json]\n"
           "       taos-window mode-daemon      (always on: new windows follow the mode of their workspace)\n"
           "  <id> is a window id from `list`; `focused` is the focused window; `last` (restore, focus) is the\n"
           "  window minimised most recently. `minimize` and `maximize` are accepted spellings. `full` toggles full size;\n"
           "  `maximise` only enters it; `restore` brings back a minimised window, or else leaves full screen and full size.\n";
    return 2;
}

static int runMode(const QStringList &pos, const QString &workspaceArg, bool json, TaosWin::WindowController &c,
                   QTextStream &out, QTextStream &err) {
    using namespace TaosWin;
    if (pos.isEmpty()) return usage(err);
    const QString sub = pos[0];
    QString e;
    DesktopModes modes;
    if (!Modes::read(Modes::path(), &modes, &e)) { err << "taos-window: " << e << "\n"; return 1; }
    Tree t;
    if (!c.list(&t, &e)) { err << "taos-window: " << e << "\n"; return 1; }
    if (sub == QLatin1String("list")) {
        QJsonArray a;
        for (const Workspace &w : std::as_const(t.workspaces))
            a << QJsonObject{{QStringLiteral("name"), w.name}, {QStringLiteral("mode"), modes.modeFor(w.name)}};
        if (json) out << QJsonDocument(a).toJson(QJsonDocument::Indented);
        else for (const Workspace &w : std::as_const(t.workspaces)) out << w.name << "\t" << modes.modeFor(w.name) << "\n";
        return 0;
    }
    const QString ws = workspaceArg.isEmpty() ? t.focusedWorkspace : workspaceArg;
    if (ws.isEmpty()) { err << "taos-window: no workspace is focused; use --workspace <name>\n"; return 1; }
    if (sub == QLatin1String("get")) { out << modes.modeFor(ws) << "\n"; return 0; }
    QString want = sub;
    if (sub == QLatin1String("toggle")) want = modes.modeFor(ws) == QLatin1String("desktop") ? QStringLiteral("tiling") : QStringLiteral("desktop");
    if (!Modes::valid(want)) return usage(err);
    // Save first: the dock and the helper follow the file; then bring the workspace's windows into line.
    modes.workspaces.insert(ws, want);
    if (!Modes::write(Modes::path(), modes, &e)) { err << "taos-window: " << e << "\n"; return 1; }
    const QMap<int, QRect> targets = modeSwitchTargets(t, ws);
    for (const QString &cmd : modeSwitchCommands(t, ws, want))
        if (!c.runRaw(cmd, &e)) { err << "taos-window: " << e << "\n"; return 1; }
    if (want == QLatin1String("desktop"))
        for (auto it = targets.begin(); it != targets.end(); ++it) c.settleGeometry(it.key(), it.value());
    out << want << "\n";
    return 0;
}

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    QTextStream out(stdout), err(stderr);
    QStringList args = app.arguments().mid(1);
    if (args.isEmpty()) return usage(err);
    QString cmd = args.takeFirst();
    if (cmd == QLatin1String("minimize")) cmd = QStringLiteral("minimise");
    if (cmd == QLatin1String("toggle-minimize")) cmd = QStringLiteral("toggle-minimise");
    if (cmd == QLatin1String("maximize")) cmd = QStringLiteral("maximise");
    bool json = false;
    QString workspaceArg;
    QStringList pos;
    for (int i = 0; i < args.size(); ++i) {
        const QString a = args[i];
        if (a == QLatin1String("--json")) json = true;
        else if (a == QLatin1String("--workspace") && i + 1 < args.size()) workspaceArg = args[++i];
        else if (a.startsWith(QLatin1String("--workspace="))) workspaceArg = a.mid(12);
        else if (a.startsWith(QLatin1String("--"))) { err << "taos-window: unknown option " << a << "\n"; return usage(err); }
        else pos << a;
    }
    SwayTransport tx;
    QString e;
    if (!tx.connectNow(&e)) { err << "taos-window: " << e << "\n"; return 3; }
    WindowController c(&tx);

    if (cmd == QLatin1String("list")) {
        if (!pos.isEmpty()) return usage(err);
        Tree t;
        if (!c.list(&t, &e)) { err << "taos-window: " << e << "\n"; return 1; }
        if (json) {
            QJsonArray a;
            for (const Window &w : std::as_const(t.windows)) a << w.toJson();
            out << QJsonDocument(a).toJson(QJsonDocument::Indented);
        } else {
            for (const Window &w : std::as_const(t.windows))
                out << w.id << "\t" << w.appId << "\t" << (w.minimised ? "minimised" : (w.focused ? "focused" : "")) << "\t"
                    << w.workspace << "\t" << w.title << "\n";
        }
        return 0;
    }
    if (cmd == QLatin1String("subscribe")) {
        if (!pos.isEmpty() || json) return usage(err);
        std::signal(SIGINT, onSignal);
        std::signal(SIGTERM, onSignal);
        std::signal(SIGPIPE, SIG_IGN);
        bool broken = false;
        const bool ok = followWindows(QString(), [&](const WindowEvent &ev) {
            const QByteArray line = QJsonDocument(ev.toJson()).toJson(QJsonDocument::Compact) + '\n';
            if (std::fwrite(line.constData(), 1, size_t(line.size()), stdout) != size_t(line.size()) || std::fflush(stdout) != 0)
                broken = true;
        }, [&] { return g_stop != 0 || broken; }, &e);
        if (!ok) { err << "taos-window: " << e << "\n"; return 3; }
        return 0;
    }
    if (cmd == QLatin1String("mode-daemon")) {
        std::signal(SIGINT, onSignal);
        std::signal(SIGTERM, onSignal);
        if (!runModeDaemon(QString(), [] { return g_stop != 0; }, &e)) { err << "taos-window: " << e << "\n"; return 3; }
        return 0;
    }
    if (cmd == QLatin1String("mode")) return runMode(pos, workspaceArg, json, c, out, err);
    if (cmd == QLatin1String("spaces")) {
        if (!pos.isEmpty()) return usage(err);
        Tree t;
        if (!c.list(&t, &e)) { err << "taos-window: " << e << "\n"; return 1; }
        if (json) {
            QJsonArray a;
            for (const Workspace &w : std::as_const(t.workspaces)) a << w.toJson();
            out << QJsonDocument(a).toJson(QJsonDocument::Indented);
        } else {
            for (const Workspace &w : std::as_const(t.workspaces))
                out << w.name << "\t" << w.output << "\t" << (w.focused ? "focused" : (w.visible ? "visible" : "")) << "\t" << w.count << "\n";
        }
        return 0;
    }
    if (cmd == QLatin1String("new-space")) {
        if (pos.size() > 1) return usage(err);
        QString created;
        if (!c.newSpace(pos.value(0), &created, &e)) { err << "taos-window: " << e << "\n"; return 1; }
        out << created << "\n";
        return 0;
    }
    if (cmd == QLatin1String("move-to-space")) {
        if (pos.isEmpty() || pos.size() > 2) return usage(err);
        if (!c.moveToSpace(pos[0], pos.value(1), &e)) { err << "taos-window: " << e << "\n"; return 1; }
        return 0;
    }
    if (cmd == QLatin1String("fullscreen") && pos.size() == 2) {
        const QString m = pos[1];
        const Plans::Fullscreen mode = m == QLatin1String("on") ? Plans::Fullscreen::On
                                       : m == QLatin1String("off") ? Plans::Fullscreen::Off : Plans::Fullscreen::Toggle;
        if (m != QLatin1String("on") && m != QLatin1String("off") && m != QLatin1String("toggle")) return usage(err);
        if (!c.fullscreen(pos[0], &e, mode)) { err << "taos-window: " << e << "\n"; return 1; }
        return 0;
    }
    if (pos.size() != 1) return usage(err);
    const QString ref = pos[0];
    bool ok = false;
    if (cmd == QLatin1String("close")) ok = c.close(ref, &e);
    else if (cmd == QLatin1String("minimise")) ok = c.minimise(ref, &e);
    else if (cmd == QLatin1String("restore")) ok = c.restore(ref, &e);
    else if (cmd == QLatin1String("toggle-minimise")) ok = c.toggleMinimise(ref, &e);
    else if (cmd == QLatin1String("focus")) ok = c.focus(ref, &e);
    else if (cmd == QLatin1String("full")) ok = c.full(ref, &e);
    else if (cmd == QLatin1String("maximise")) ok = c.maximise(ref, &e);
    else if (cmd == QLatin1String("bring-here")) ok = c.bringHere(ref, &e);
    else if (cmd == QLatin1String("reset-size")) ok = c.resetSize(ref, &e);
    else if (cmd == QLatin1String("fullscreen")) ok = c.fullscreen(ref, &e);
    else return usage(err);
    if (!ok) { err << "taos-window: " << e << "\n"; return 1; }
    return 0;
}
