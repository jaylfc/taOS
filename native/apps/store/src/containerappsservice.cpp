// SPDX-License-Identifier: AGPL-3.0-or-later
#include "containerappsservice.h"

#include <QCoreApplication>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QProcess>

using namespace Qt::StringLiterals;

namespace Taos::Store {

ContainerAppsService::ContainerAppsService(QObject *parent) : QObject(parent) {}

QString ContainerAppsService::program()
{
    const QString env = qEnvironmentVariable("TAOS_APPGROUPS_BIN");
    if (!env.isEmpty())
        return env;
    const QString sibling = QCoreApplication::applicationDirPath() + u"/taos-appgroups"_s;
    return QFileInfo::exists(sibling) ? sibling : u"taos-appgroups"_s;
}

static QJsonObject lastJson(const QByteArray &out)
{
    const QList<QByteArray> lines = out.trimmed().split('\n');
    return lines.isEmpty() ? QJsonObject() : QJsonDocument::fromJson(lines.last()).object();
}

QJsonArray ContainerAppsService::catalog() const
{
    QProcess p;
    p.start(program(), {u"catalog"_s, u"--json"_s});
    if (!p.waitForFinished(5000) || p.exitCode() != 0)
        return {};
    QJsonArray out;
    for (const QJsonValue &v : lastJson(p.readAllStandardOutput()).value(u"apps"_s).toArray()) {
        QJsonObject o = v.toObject();
        if (o.contains(u"invalid"_s))
            continue;
        o[u"type"_s] = u"containerapp"_s;
        o[u"category"_s] = o.value(u"categories"_s).toArray().isEmpty() ? u"apps"_s : o.value(u"categories"_s).toArray().first().toString().toLower();
        out.append(o);
    }
    return out;
}

QSet<QString> ContainerAppsService::installedIds() const
{
    QProcess p;
    p.start(program(), {u"list"_s, u"--json"_s});
    QSet<QString> ids;
    if (!p.waitForFinished(5000) || p.exitCode() != 0)
        return ids;
    for (const QJsonValue &v : lastJson(p.readAllStandardOutput()).value(u"apps"_s).toArray())
        ids.insert(v.toObject().value(u"id"_s).toString());
    return ids;
}

static void runStreaming(QObject *owner, ContainerAppsService *svc, const QString &id, const QStringList &args, const QString &okText,
                         const QString &failPrefix)
{
    auto *p = new QProcess(owner);
    auto buf = std::make_shared<QByteArray>();
    QObject::connect(p, &QProcess::readyReadStandardOutput, owner, [=] {
        buf->append(p->readAllStandardOutput());
        int nl;
        while ((nl = buf->indexOf('\n')) >= 0) {
            const QJsonObject o = QJsonDocument::fromJson(buf->left(nl)).object();
            buf->remove(0, nl + 1);
            if (o.value(u"event"_s).toString() == u"step"_s)
                Q_EMIT svc->progress(id, o.value(u"n"_s).toInt(), o.value(u"of"_s).toInt(), o.value(u"text"_s).toString());
        }
    });
    QObject::connect(p, &QProcess::finished, owner, [=](int code) {
        const QJsonObject done = lastJson(*buf + p->readAllStandardOutput());
        const bool ok = code == 0;
        // On failure the engine's own sentence is shown; nothing is guessed here.
        Q_EMIT svc->finished(id, ok, ok ? okText : (done.value(u"error"_s).toString().isEmpty() ? failPrefix : done.value(u"error"_s).toString()));
        p->deleteLater();
    });
    QObject::connect(p, &QProcess::errorOccurred, owner, [=](QProcess::ProcessError e) {
        if (e == QProcess::FailedToStart) {
            Q_EMIT svc->finished(id, false, QObject::tr("The app installer (taos-appgroups) is not installed on this computer."));
            p->deleteLater();
        }
    });
    p->start(ContainerAppsService::program(), args);
}

void ContainerAppsService::install(const QString &id, const QString &target, bool acceptedHostWarning)
{
    QStringList a = {u"install"_s, id, u"--json"_s, u"--target"_s, target.isEmpty() ? u"group:default"_s : target};
    if (acceptedHostWarning)
        a << u"--accept-host-warning"_s;
    runStreaming(this, this, id, a, tr("Installed."), tr("The install did not finish."));
}

void ContainerAppsService::remove(const QString &id, bool purgeData)
{
    QStringList a = {u"remove"_s, id, u"--json"_s};
    if (purgeData)
        a << u"--purge-data"_s;
    runStreaming(this, this, id, a, tr("Removed."), tr("The removal did not finish."));
}

bool ContainerAppsService::open(const QString &id, const QStringList &args)
{
    return QProcess::startDetached(program(), QStringList{u"run"_s, id} + args);
}

} // namespace Taos::Store
