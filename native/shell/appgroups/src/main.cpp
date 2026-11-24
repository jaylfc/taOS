// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-appgroups: app groups and the desktop apps installed in them, on the command line. Everything the Store does,
// an agent can do here. Every command takes --json (one JSON object on stdout; install and group create also stream
// one {"event":...} object per line before the final {"ok":...} line).
//
// Exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 incus unavailable or not allowed, 5 refused (invalid manifest,
// unsupported machine, policy), 6 an install step failed, 7 conflict (already exists), 8 no desktop session, 9 target not available yet.
#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTextStream>

#include "appgroups.h"

using namespace Qt::StringLiterals;
using namespace TaosAppGroups;

namespace {

void out(const QString &s) { QTextStream(stdout) << s << '\n'; }

QString usage()
{
    return uR"(usage: taos-appgroups <command> [--json]
  ensure-default                 create the default app group ("My apps") if it is missing
  groups                         list your app groups
  group create <name>            create another app group (its own container and grant set)
  catalog                        apps that can be installed
  list [--available]             installed apps (and what else could be installed)
  install <app-id> [--target T]  install an app; T is group:<name> (default group:default), host or worker:<name>
                                 (host and worker are not available yet; --accept-host-warning is carried for the Store)
  remove <app-id> [--purge-data] remove an app; its data is kept unless --purge-data
  run <app-id> [args...]         show the app in the current desktop session
  launchers sync                 rewrite the launcher entries of installed apps
  status                         containers, grants and devices
exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 incus unavailable, 5 refused, 6 install step failed,
            7 conflict, 8 no desktop session, 9 target not available yet)"_s;
}

QString line(const QJsonObject &o) { return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact)); }

int finish(const Result &r, bool json, const std::function<QString(const QJsonObject &)> &text)
{
    if (json) {
        QJsonObject o = r.data;
        o[u"event"_s] = u"done"_s;
        o[u"ok"_s] = r.ok;
        if (!r.ok) {
            o[u"error"_s] = r.error;
            o[u"code"_s] = r.exit;
        }
        out(line(o));
    } else if (r.ok) {
        const QString t = text(r.data);
        if (!t.isEmpty())
            out(t);
    } else {
        QTextStream(stderr) << "taos-appgroups: " << r.error << '\n';
    }
    return r.ok ? 0 : r.exit;
}

} // namespace

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    QStringList a = app.arguments().mid(1);
    bool json = false, available = false, purge = false, acceptHost = false;
    QString target;
    QString group = QString::fromLatin1(kDefaultGroup);
    QStringList pos, rest;
    bool afterRun = false;
    for (int i = 0; i < a.size(); ++i) {
        const QString &s = a[i];
        if (afterRun) {
            rest << s;
        } else if (s == u"--json"_s) {
            json = true;
        } else if (s == u"--available"_s) {
            available = true;
        } else if (s == u"--purge-data"_s) {
            purge = true;
        } else if (s == u"--accept-host-warning"_s) {
            acceptHost = true;
        } else if (s == u"--target"_s && i + 1 < a.size()) {
            target = a[++i];
        } else if (s == u"--group"_s && i + 1 < a.size()) {
            group = a[++i];
        } else if (s == u"--help"_s || s == u"-h"_s) {
            out(usage());
            return 0;
        } else {
            pos << s;
            if (pos.size() == 2 && pos[0] == u"run"_s)
                afterRun = true;
        }
    }
    if (pos.isEmpty()) {
        QTextStream(stderr) << usage() << '\n';
        return ExitUsage;
    }
    Engine e;
    const Progress prog = [json](const QJsonObject &ev) {
        if (json) {
            out(line(ev));
        } else if (ev.value(u"event"_s).toString() == u"step"_s) {
            out(u"[%1/%2] %3"_s.arg(ev.value(u"n"_s).toInt()).arg(ev.value(u"of"_s).toInt()).arg(ev.value(u"text"_s).toString()));
        }
    };
    const QString cmd = pos[0];
    if (cmd == u"ensure-default"_s)
        return finish(e.ensureDefault(prog), json, [](const QJsonObject &d) {
            return u"%1 is ready (container %2)"_s.arg(d.value(u"display_name"_s).toString(), d.value(u"container"_s).toString());
        });
    if (cmd == u"groups"_s)
        return finish(e.groups(), json, [](const QJsonObject &d) {
            QStringList l;
            for (const QJsonValue &v : d.value(u"groups"_s).toArray()) {
                const QJsonObject g = v.toObject();
                l << u"%1\t%2\t%3\t%4 apps"_s.arg(g.value(u"name"_s).toString(), g.value(u"display_name"_s).toString(),
                                                g.value(u"state"_s).toString()).arg(g.value(u"apps"_s).toArray().size());
            }
            return l.isEmpty() ? u"no app groups yet (taos-appgroups ensure-default)"_s : l.join(u'\n');
        });
    if (cmd == u"group"_s && pos.value(1) == u"create"_s && pos.size() == 3)
        return finish(e.groupCreate(pos[2], prog), json, [](const QJsonObject &d) { return u"created group %1"_s.arg(d.value(u"group"_s).toString()); });
    if (cmd == u"catalog"_s)
        return finish(e.catalog(), json, [](const QJsonObject &d) {
            QStringList l;
            for (const QJsonValue &v : d.value(u"apps"_s).toArray())
                l << u"%1\t%2"_s.arg(v.toObject().value(u"id"_s).toString(), v.toObject().value(u"name"_s).toString());
            return l.join(u'\n');
        });
    if (cmd == u"list"_s)
        return finish(e.list(available), json, [](const QJsonObject &d) {
            QStringList l;
            for (const QJsonValue &v : d.value(u"apps"_s).toArray())
                l << u"%1\t%2\tgroup %3"_s.arg(v.toObject().value(u"id"_s).toString(), v.toObject().value(u"name"_s).toString(), v.toObject().value(u"group"_s).toString());
            for (const QJsonValue &v : d.value(u"available"_s).toArray())
                l << u"%1\t%2\tnot installed"_s.arg(v.toObject().value(u"id"_s).toString(), v.toObject().value(u"name"_s).toString());
            return l.isEmpty() ? u"nothing installed"_s : l.join(u'\n');
        });
    if (cmd == u"install"_s && pos.size() == 2) {
        if (!target.isEmpty()) {
            const Result t = Engine::parseTarget(target, acceptHost, &group);
            if (!t.ok)
                return finish(t, json, [](const QJsonObject &) { return QString(); });
        }
        return finish(e.install(pos[1], group, prog), json, [](const QJsonObject &d) { return u"installed %1 into %2"_s.arg(d.value(u"id"_s).toString(), d.value(u"container"_s).toString()); });
    }
    if (cmd == u"remove"_s && pos.size() == 2)
        return finish(e.remove(pos[1], purge, prog), json, [](const QJsonObject &d) {
            return d.value(u"data_kept"_s).toBool() ? u"removed %1 (its data was kept)"_s.arg(d.value(u"id"_s).toString()) : u"removed %1 and its data"_s.arg(d.value(u"id"_s).toString());
        });
    if (cmd == u"run"_s && pos.size() >= 2)
        return finish(e.run(pos[1], rest), json, [](const QJsonObject &) { return QString(); });
    if (cmd == u"launchers"_s && pos.value(1) == u"sync"_s)
        return finish(e.syncLaunchers(), json, [](const QJsonObject &d) { return u"wrote %1 launcher entries"_s.arg(d.value(u"launchers"_s).toArray().size()); });
    if (cmd == u"status"_s)
        return finish(e.status(), json, [](const QJsonObject &d) {
            QStringList l = {u"incus path: %1; isolation between people: %2"_s.arg(d.value(u"incus_path"_s).toString(), d.value(u"isolation_between_people"_s).toString())};
            for (const QJsonValue &v : d.value(u"groups"_s).toArray()) {
                const QJsonObject g = v.toObject();
                l << u"%1 (%2) %3, grants %4"_s.arg(g.value(u"name"_s).toString(), g.value(u"container"_s).toString(), g.value(u"state"_s).toString(),
                                                   QString::fromUtf8(QJsonDocument(g.value(u"grants"_s).toObject()).toJson(QJsonDocument::Compact)));
            }
            return l.join(u'\n');
        });
    QTextStream(stderr) << usage() << '\n';
    return ExitUsage;
}
