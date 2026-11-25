// SPDX-License-Identifier: AGPL-3.0-or-later
#include "applaunch.h"

#include <QDateTime>
#include <QDir>
#include <QHash>
#include <QMutex>
#include <QFileInfo>
#include <QProcess>
#include <QRegularExpression>
#include <QStandardPaths>

namespace taos {

QProcessEnvironment cleanAppEnvironment(const QProcessEnvironment &in) {
    QProcessEnvironment out = in;
    // 1. names a component published as "I set these for myself" (the launcher does this in TAOS_LAUNCHER_OWN_ENV)
    for (const QString &n : in.value(QStringLiteral("TAOS_LAUNCHER_OWN_ENV")).split(QLatin1Char(','), Qt::SkipEmptyParts)) out.remove(n.trimmed());
    out.remove(QStringLiteral("TAOS_LAUNCHER_OWN_ENV"));
    // 2. the layer-shell integration is only ever right for a shell component's own surface
    out.remove(QStringLiteral("QT_WAYLAND_SHELL_INTEGRATION"));
    // 3. what the session supervisor gives its components. TAOS_SHELL_ENV_FILE stays: the app wrappers source it.
    for (const QString &k : in.keys())
        if (k.startsWith(QLatin1String("TAOS_SHELL_")) && k != QLatin1String("TAOS_SHELL_ENV_FILE")) out.remove(k);
    return out;
}

// Does a transient user scope really start here? (No user manager, a stale XDG_RUNTIME_DIR, a sandbox: it does not, and
// then the app is started directly rather than not at all.) Asked once per environment, then remembered.
static bool scopeWorks(const QString &systemdRun, const QProcessEnvironment &env) {
    static QMutex mu;
    static QHash<QString, bool> known;
    const QString key = env.value(QStringLiteral("XDG_RUNTIME_DIR")) + QLatin1Char('|') + env.value(QStringLiteral("DBUS_SESSION_BUS_ADDRESS"));
    { QMutexLocker l(&mu); const auto it = known.constFind(key); if (it != known.constEnd()) return *it; }
    QProcess p;
    p.setProcessEnvironment(env);
    p.start(systemdRun, { QStringLiteral("--user"), QStringLiteral("--scope"), QStringLiteral("--quiet"), QStringLiteral("--collect"), QStringLiteral("true") });
    const bool ok = p.waitForFinished(4000) && p.exitStatus() == QProcess::NormalExit && p.exitCode() == 0;
    if (p.state() != QProcess::NotRunning) { p.kill(); p.waitForFinished(200); }
    QMutexLocker l(&mu);
    known.insert(key, ok);
    return ok;
}

static QString unitName(const QString &program) {
    QString base = QFileInfo(program).fileName();
    base.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9_.-]")), QStringLiteral("_"));
    static int n = 0;
    return QStringLiteral("taos-app-%1-%2-%3").arg(base.left(40)).arg(QDateTime::currentMSecsSinceEpoch()).arg(++n);
}

bool launchApp(const QString &program, const QStringList &args, QString *error, const LaunchOptions &o) {
    if (program.isEmpty()) { if (error) *error = QStringLiteral("nothing to run"); return false; }
    const bool path = program.contains(QLatin1Char('/'));
    const QString exe = path ? (QFileInfo(program).isExecutable() ? program : QString()) : QStandardPaths::findExecutable(program);
    if (exe.isEmpty()) { if (error) *error = QStringLiteral("could not start %1").arg(program); return false; }
    const QProcessEnvironment base = o.base ? *o.base : QProcessEnvironment::systemEnvironment();
    const QProcessEnvironment env = cleanAppEnvironment(base);
    QString prog = program;
    QStringList argv = args;
    const QString sd = o.useScope && env.contains(QStringLiteral("XDG_RUNTIME_DIR")) ? QStandardPaths::findExecutable(QStringLiteral("systemd-run")) : QString();
    if (!sd.isEmpty() && scopeWorks(sd, env)) {
        // a transient scope of the person's user manager: the app is not the component's child and has its own cgroup
        argv = { QStringLiteral("--user"), QStringLiteral("--scope"), QStringLiteral("--quiet"), QStringLiteral("--collect"),
                 QStringLiteral("--unit=") + unitName(program), QStringLiteral("--"), program };
        argv << args;
        prog = sd;
    }
    QProcess p;
    p.setProgram(prog);
    p.setArguments(argv);
    p.setWorkingDirectory(o.workingDirectory.isEmpty() ? QDir::homePath() : o.workingDirectory);
    p.setProcessEnvironment(env);
    if (!p.startDetached()) {
        if (error) *error = QStringLiteral("could not start %1").arg(program);
        return false;
    }
    return true;
}

}  // namespace taos
