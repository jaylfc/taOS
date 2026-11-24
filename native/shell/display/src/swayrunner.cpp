// SPDX-License-Identifier: AGPL-3.0-or-later
#include "swayrunner.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>

SwayMsg::SwayMsg(QString program) : m_program(std::move(program)) {
    if (m_program.isEmpty()) m_program = qEnvironmentVariable("TAOS_SWAYMSG", QStringLiteral("swaymsg"));
}

SwayRunner::Result SwayMsg::run(const QStringList &args) {
    Result r;
    QProcess p;
    p.start(m_program, args);
    if (!p.waitForStarted(3000)) { r.error = QStringLiteral("cannot run %1").arg(m_program); return r; }
    if (!p.waitForFinished(8000)) { p.kill(); p.waitForFinished(1000); r.error = QStringLiteral("%1 timed out").arg(m_program); return r; }
    r.out = p.readAllStandardOutput();
    if (p.exitStatus() != QProcess::NormalExit || p.exitCode() != 0) {
        r.error = QString::fromUtf8(p.readAllStandardError()).trimmed();
        if (r.error.isEmpty()) r.error = QString::fromUtf8(r.out).trimmed();
        if (r.error.isEmpty()) r.error = QStringLiteral("%1 failed").arg(m_program);
        return r;
    }
    // A command can fail with exit 0 in some versions: the reply is a JSON array of {success,error}.
    if (!args.contains(QStringLiteral("-t"))) {
        const QJsonDocument d = QJsonDocument::fromJson(r.out);
        if (d.isArray())
            for (const QJsonValue &v : d.array()) {
                const QJsonObject o = v.toObject();
                if (o.contains(QLatin1String("success")) && !o.value(QLatin1String("success")).toBool()) {
                    r.error = o.value(QLatin1String("error")).toString(QStringLiteral("command failed"));
                    return r;
                }
            }
    }
    r.ok = true;
    return r;
}
