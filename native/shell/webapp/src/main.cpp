// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-webapp: the web apps engine on the command line. Everything the Store and Settings do, an agent can do here.
#include <QCoreApplication>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTextStream>

#include "webapp.h"

using namespace Qt::StringLiterals;
using namespace TaosWebapp;

namespace {

struct Args {
    QStringList pos;
    QHash<QString, QString> opt;   // --name value, --flag -> "1"
    bool has(const QString &k) const { return opt.contains(k); }
    QString get(const QString &k) const { return opt.value(k); }
};

Args parse(const QStringList &a, const QSet<QString> &valued)
{
    Args r;
    for (int i = 0; i < a.size(); ++i) {
        const QString &s = a[i];
        if (s.startsWith(u"--"_s) && s.size() > 2) {
            const int eq = s.indexOf(u'=');
            if (eq > 0) {
                r.opt.insert(s.mid(2, eq - 2), s.mid(eq + 1));
            } else {
                const QString k = s.mid(2);
                if (valued.contains(k) && i + 1 < a.size())
                    r.opt.insert(k, a[++i]);
                else
                    r.opt.insert(k, u"1"_s);
            }
        } else {
            r.pos << s;
        }
    }
    return r;
}

void out(const QString &s)
{
    QTextStream(stdout) << s << '\n';
}

QString human(qint64 bytes)
{
    if (bytes < 1024)
        return u"%1 B"_s.arg(bytes);
    const double k = bytes / 1024.0;
    if (k < 1024)
        return u"%1 KB"_s.arg(k, 0, 'f', 0);
    const double m = k / 1024.0;
    if (m < 1024)
        return u"%1 MB"_s.arg(m, 0, 'f', 1);
    return u"%1 GB"_s.arg(m / 1024.0, 0, 'f', 2);
}

int finish(const Result &r, bool json, const std::function<QString(const QJsonObject &)> &text)
{
    if (json) {
        QJsonObject o = r.data;
        if (!r.ok) {
            o[u"error"_s] = r.error;
            o[u"code"_s] = r.code;
        }
        out(QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact)));
    } else if (r.ok) {
        const QString t = text(r.data);
        if (!t.isEmpty())
            out(t);
    } else {
        QTextStream(stderr) << "taos-webapp: " << r.error << '\n';
    }
    if (r.ok)
        return 0;
    return r.code == u"no_browser"_s ? 3 : 1;
}

const char *kUsage =
    "usage: taos-webapp <command> [options]\n"
    "  list [--json]\n"
    "  add --url URL [--name NAME] [--icon PATH|auto] [--from-store ID] [--allow-http] [--browser NAME] [--json]\n"
    "  launch ID\n"
    "  clone ID [--name NAME] [--json]\n"
    "  rename ID NAME\n"
    "  remove ID [--keep-data] [--force]\n"
    "  clear-data ID [--force]\n"
    "  info ID [--json]\n"
    "  doctor [--json]\n"
    "Every instance has its own private storage and sign-in. Data: $XDG_DATA_HOME/taos/webapps\n"
    "Exit status: 0 done, 1 refused or failed, 2 usage, 3 no browser engine installed.\n";

} // namespace

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    const QStringList all = app.arguments().mid(1);
    if (all.isEmpty() || all.first() == u"--help"_s || all.first() == u"-h"_s) {
        fputs(kUsage, all.isEmpty() ? stderr : stdout);
        return all.isEmpty() ? 2 : 0;
    }
    const QString cmd = all.first();
    const Args a = parse(all.mid(1), {u"url"_s, u"name"_s, u"icon"_s, u"from-store"_s, u"browser"_s});
    const bool json = a.has(u"json"_s);
    Engine e;
    if (cmd == u"list"_s || cmd == u"doctor"_s || cmd == u"info"_s)
        e.syncLaunchers();   // instances from before the window id was known get their launcher entry corrected

    auto needId = [&](QString *id) {
        if (a.pos.isEmpty()) {
            QTextStream(stderr) << "taos-webapp: " << cmd << " needs a web app id\n";
            return false;
        }
        *id = a.pos.first();
        return true;
    };

    if (cmd == u"list"_s) {
        QJsonArray arr;
        for (const Instance &i : e.list()) {
            QJsonObject o = i.toJson();
            o[u"running"_s] = e.isRunning(i.id);
            o[u"size_bytes"_s] = e.profileSize(i.id);
            arr.append(o);
        }
        if (json) {
            out(QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
        } else if (arr.isEmpty()) {
            out(u"No web apps yet. Add one with: taos-webapp add --url https://example.com"_s);
        } else {
            for (const QJsonValue &v : std::as_const(arr)) {
                const QJsonObject o = v.toObject();
                out(u"%1  %2  %3%4"_s.arg(o[u"id"_s].toString(), o[u"name"_s].toString().leftJustified(24), o[u"url"_s].toString(),
                                          o[u"running"_s].toBool() ? u"  (open)"_s : QString()));
            }
        }
        return 0;
    }
    if (cmd == u"add"_s) {
        AddRequest r;
        r.url = a.get(u"url"_s);
        if (r.url.isEmpty() && !a.pos.isEmpty())
            r.url = a.pos.first();
        if (r.url.isEmpty()) {
            QTextStream(stderr) << "taos-webapp: add needs --url\n";
            return 2;
        }
        r.name = a.get(u"name"_s);
        r.icon = a.get(u"icon"_s);
        if (a.has(u"from-store"_s))
            r.source = a.get(u"from-store"_s);
        if (a.has(u"browser"_s))
            r.browser = a.get(u"browser"_s);
        r.allowHttp = a.has(u"allow-http"_s);
        return finish(e.add(r), json, [](const QJsonObject &d) {
            return u"Added %1 (%2)%3"_s.arg(d[u"name"_s].toString(), d[u"id"_s].toString(),
                                            d[u"icon_fallback"_s].toBool() ? u", with a generated icon"_s : QString());
        });
    }
    QString id;
    if (cmd == u"launch"_s) {
        if (!needId(&id))
            return 2;
        return finish(e.launch(id), json, [](const QJsonObject &) { return QString(); });
    }
    if (cmd == u"clone"_s) {
        if (!needId(&id))
            return 2;
        return finish(e.clone(id, a.get(u"name"_s)), json,
                      [](const QJsonObject &d) { return u"Cloned to %1 (%2), signed in as before"_s.arg(d[u"name"_s].toString(), d[u"id"_s].toString()); });
    }
    if (cmd == u"rename"_s) {
        if (!needId(&id))
            return 2;
        if (a.pos.size() < 2) {
            QTextStream(stderr) << "taos-webapp: rename needs a new name\n";
            return 2;
        }
        return finish(e.rename(id, a.pos.mid(1).join(u' ')), json, [](const QJsonObject &d) { return u"Renamed to %1"_s.arg(d[u"name"_s].toString()); });
    }
    if (cmd == u"remove"_s) {
        if (!needId(&id))
            return 2;
        return finish(e.remove(id, a.has(u"keep-data"_s), a.has(u"force"_s)), json, [&](const QJsonObject &) { return u"Removed %1"_s.arg(id); });
    }
    if (cmd == u"clear-data"_s) {
        if (!needId(&id))
            return 2;
        return finish(e.clearData(id, a.has(u"force"_s)), json, [&](const QJsonObject &) { return u"Cleared the data of %1"_s.arg(id); });
    }
    if (cmd == u"info"_s) {
        if (!needId(&id))
            return 2;
        return finish(e.info(id), json, [](const QJsonObject &d) {
            QStringList l;
            l << u"%1 (%2)"_s.arg(d[u"name"_s].toString(), d[u"id"_s].toString());
            l << u"  url:       %1"_s.arg(d[u"url"_s].toString());
            l << u"  source:    %1"_s.arg(d[u"source"_s].toString());
            l << u"  storage:   %1 at %2"_s.arg(human(d[u"size_bytes"_s].toInteger()), d[u"profile_dir"_s].toString());
            l << u"  last used: %1"_s.arg(d[u"last_used"_s].toString().isEmpty() ? u"never"_s : d[u"last_used"_s].toString());
            l << u"  open now:  %1"_s.arg(d[u"running"_s].toBool() ? u"yes"_s : u"no"_s);
            l << u"  window id: %1"_s.arg(d[u"app_id"_s].toString());
            if (d[u"permissions"_s].isNull())
                l << u"  permissions: not readable yet"_s;
            return l.join(u'\n');
        });
    }
    if (cmd == u"doctor"_s) {
        return finish(e.doctor(), json, [](const QJsonObject &d) {
            QStringList l;
            l << u"browser engine: %1"_s.arg(d[u"chosen"_s].toString());
            for (const QJsonValue &b : d[u"browsers"_s].toArray())
                l << u"  found %1 at %2 %3"_s.arg(b.toObject()[u"name"_s].toString(), b.toObject()[u"path"_s].toString(), b.toObject()[u"version"_s].toString());
            l << u"data folder: %1"_s.arg(d[u"root"_s].toString());
            return l.join(u'\n');
        });
    }
    QTextStream(stderr) << "taos-webapp: unknown command " << cmd << "\n" << kUsage;
    return 2;
}
