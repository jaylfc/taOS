// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runner.h"

#include <QDateTime>
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QStandardPaths>

namespace taos::search {

QStringList terminalPrefix() {
    const QString env = qEnvironmentVariable("TERMINAL");
    if (!env.isEmpty() && !QStandardPaths::findExecutable(env).isEmpty()) return { env, QStringLiteral("-e") };
    for (const QString &t : { QStringLiteral("foot"), QStringLiteral("alacritty"), QStringLiteral("kitty"), QStringLiteral("wezterm"),
                              QStringLiteral("gnome-terminal"), QStringLiteral("konsole"), QStringLiteral("xterm") }) {
        if (QStandardPaths::findExecutable(t).isEmpty()) continue;
        if (t == QLatin1String("wezterm")) return { t, QStringLiteral("start"), QStringLiteral("--") };
        if (t == QLatin1String("gnome-terminal")) return { t, QStringLiteral("--") };
        return { t, QStringLiteral("-e") };
    }
    return {};
}

QString focusedOutputFromJson(const QByteArray &json) {
    const QJsonDocument d = QJsonDocument::fromJson(json);
    if (!d.isArray()) return {};
    for (const QJsonValue &v : d.array())
        if (v.toObject().value(QStringLiteral("focused")).toBool()) return v.toObject().value(QStringLiteral("name")).toString();
    return {};
}

QString focusedOutput() {
    QProcess p;
    p.start(QStringLiteral("swaymsg"), { QStringLiteral("-t"), QStringLiteral("get_outputs"), QStringLiteral("-r") });
    if (!p.waitForFinished(500)) { p.kill(); return {}; }
    return focusedOutputFromJson(p.readAllStandardOutput());
}

// The environment a launched program gets: ours minus what the daemon set for itself (a Qt app started
// from here must not inherit QT_QPA_PLATFORM=wayland and the layer-shell integration, or it would try
// to be a layer surface). The daemon lists those names in TAOS_LAUNCHER_OWN_ENV.
QProcessEnvironment childEnvironment() {
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    for (const QString &n : env.value(QStringLiteral("TAOS_LAUNCHER_OWN_ENV")).split(QLatin1Char(','), Qt::SkipEmptyParts)) env.remove(n);
    env.remove(QStringLiteral("TAOS_LAUNCHER_OWN_ENV"));
    return env;
}

static bool detached(const QString &prog, const QStringList &args, QString *error) {
    if (prog.isEmpty()) { if (error) *error = QStringLiteral("nothing to run"); return false; }
    QProcess p;
    p.setProgram(prog);
    p.setArguments(args);
    p.setWorkingDirectory(QDir::homePath());
    p.setProcessEnvironment(childEnvironment());
    if (!p.startDetached()) {
        if (error) *error = QStringLiteral("could not start %1").arg(prog);
        return false;
    }
    return true;
}

bool runAction(const Action &a, QString *error) {
    if (a.stub) { if (error) *error = a.label; return false; }
    if (a.verb == QLatin1String("launch") || a.verb == QLatin1String("exec")) {
        QStringList argv = a.args;
        if (a.verb == QLatin1String("launch") && a.data == QLatin1String("terminal")) {
            const QStringList t = terminalPrefix();
            if (t.isEmpty()) { if (error) *error = QStringLiteral("no terminal emulator installed"); return false; }
            argv = t + argv;
        }
        if (argv.isEmpty()) { if (error) *error = QStringLiteral("empty command"); return false; }
        return detached(argv.first(), argv.mid(1), error);
    }
    if (a.verb == QLatin1String("capture-screen") || a.verb == QLatin1String("capture-region")) {
        // grim (and slurp for an area) write a PNG in Pictures/Screenshots; the short sleep lets the launcher fade out first
        const QString dir = QStandardPaths::writableLocation(QStandardPaths::PicturesLocation) + QStringLiteral("/Screenshots");
        if (!QDir().mkpath(dir)) { if (error) *error = QStringLiteral("cannot create %1").arg(dir); return false; }
        const QString file = dir + QStringLiteral("/taos-") + QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss")) + QStringLiteral(".png");
        const bool region = a.verb == QLatin1String("capture-region");
        const QString script = region ? QStringLiteral("sleep 0.3; g=$(slurp) || exit 0; exec grim -g \"$g\" \"$1\"") : QStringLiteral("sleep 0.4; exec grim \"$1\"");
        return detached(QStringLiteral("sh"), { QStringLiteral("-c"), script, QStringLiteral("taos-capture"), file }, error);
    }
    if (a.verb == QLatin1String("focus-window")) return detached(QStringLiteral("taos-window"), { QStringLiteral("focus"), a.data }, error);
    if (a.verb == QLatin1String("open")) return detached(QStringLiteral("xdg-open"), { a.data }, error);
    if (a.verb == QLatin1String("display-scale")) {
        const QString out = focusedOutput();
        if (out.isEmpty()) { if (error) *error = QStringLiteral("could not tell which screen is focused"); return false; }
        return detached(QStringLiteral("taos-display"), { QStringLiteral("scale"), out, a.data }, error);
    }
    if (error) *error = QStringLiteral("unknown action %1").arg(a.verb);
    return false;
}

}  // namespace taos::search
