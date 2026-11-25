// SPDX-License-Identifier: AGPL-3.0-or-later
#include "applaunch.h"

#include <QDateTime>
#include <QDir>
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
    if (!sd.isEmpty()) {
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
