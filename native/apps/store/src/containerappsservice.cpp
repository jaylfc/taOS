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
                         const QString &failPrefix);

QHash<QString, QJsonObject> ContainerAppsService::installedInfo() const
{
    QProcess p;
    p.start(program(), {u"list"_s, u"--json"_s});
    QHash<QString, QJsonObject> out;
    if (!p.waitForFinished(5000) || p.exitCode() != 0)
        return out;
    for (const QJsonValue &v : lastJson(p.readAllStandardOutput()).value(u"apps"_s).toArray())
        out.insert(v.toObject().value(u"id"_s).toString(), v.toObject());
    return out;
}

QJsonArray ContainerAppsService::groups() const
{
    QProcess p;
    p.start(program(), {u"groups"_s, u"--json"_s});
    if (!p.waitForFinished(8000) || p.exitCode() != 0)
        return {};
    return lastJson(p.readAllStandardOutput()).value(u"groups"_s).toArray();
}

void ContainerAppsService::createGroup(const QString &name, const QString &displayName)
{
    auto *p = new QProcess(this);
    QObject::connect(p, &QProcess::finished, this, [=](int code) {
        const QJsonObject done = lastJson(p->readAllStandardOutput());
        Q_EMIT groupCreated(name, code == 0, code == 0 ? tr("The group is ready.") : (done.value(u"error"_s).toString().isEmpty() ? tr("The group could not be created.") : done.value(u"error"_s).toString()));
        p->deleteLater();
    });
    QObject::connect(p, &QProcess::errorOccurred, this, [=](QProcess::ProcessError e) {
        if (e == QProcess::FailedToStart) {
            Q_EMIT groupCreated(name, false, tr("The app installer (taos-appgroups) is not installed on this computer."));
            p->deleteLater();
        }
    });
    p->start(program(), {u"group"_s, u"create"_s, name, u"--display-name"_s, displayName, u"--json"_s});
}

void ContainerAppsService::move(const QString &id, const QString &group)
{
    runStreaming(this, this, id, {u"move"_s, id, u"--to"_s, u"group:"_s + group, u"--json"_s}, tr("Moved."), tr("The move did not finish."));
}

static void runStreaming(QObject *owner, ContainerAppsService *svc, const QString &id, const QStringList &args, const QString &okText,
                         const QString &failPrefix)
{
    auto *p = new QProcess(owner);
    auto buf = std::make_shared<QByteArray>();
    auto doneLine = std::make_shared<QJsonObject>();   // the engine's closing line, even when it was read with the progress lines
    QObject::connect(p, &QProcess::readyReadStandardOutput, owner, [=] {
        buf->append(p->readAllStandardOutput());
        int nl;
        while ((nl = buf->indexOf('\n')) >= 0) {
            const QJsonObject o = QJsonDocument::fromJson(buf->left(nl)).object();
            buf->remove(0, nl + 1);
            if (o.value(u"event"_s).toString() == u"done"_s)
                *doneLine = o;
            if (o.value(u"event"_s).toString() == u"step"_s)
                Q_EMIT svc->progress(id, o.value(u"n"_s).toInt(), o.value(u"of"_s).toInt(), o.value(u"text"_s).toString());
        }
    });
    QObject::connect(p, &QProcess::finished, owner, [=](int code) {
        const QJsonObject rest = lastJson(*buf + p->readAllStandardOutput());
        const QJsonObject done = rest.isEmpty() ? *doneLine : rest;
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

void ContainerAppsService::install(const QString &id, const QString &target, bool acceptedHostWarning, bool licenceAccepted)
{
    QStringList a = {u"install"_s, id, u"--json"_s, u"--target"_s, target.isEmpty() ? u"group:default"_s : target};
    if (licenceAccepted)
        a << u"--accept-licence"_s << id << u"--accepted-by"_s << qEnvironmentVariable("USER", u"the person using this computer"_s) + u" (in the Store)"_s;
    if (acceptedHostWarning)
        a << u"--accept-host-warning"_s;
    runStreaming(this, this, id, a, tr("Installed."), tr("The install did not finish."));
}

bool ContainerAppsService::acceptLicence(const QString &id)
{
    QProcess p;
    p.start(program(), {u"licence"_s, u"accept"_s, id, u"--accepted-by"_s, qEnvironmentVariable("USER", u"the person using this computer"_s) + u" (in the Store)"_s, u"--json"_s});
    return p.waitForFinished(8000) && p.exitCode() == 0;
}

void ContainerAppsService::update(const QString &id)
{
    runStreaming(this, this, id, {u"update"_s, id, u"--json"_s}, tr("Updated."), tr("The update did not finish."));
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
