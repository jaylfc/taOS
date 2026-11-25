// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-search: the launcher's search engine without the launcher. For a TUI over SSH, scripts and agents.
//   taos-search [--json] [--timeout MS] [--limit N] "query"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimer>
#include <cstdio>

#include "actionmenu.h"
#include "appmenu.h"
#include "engine.h"
#include "jsonio.h"
#include "runner.h"
#include "setup.h"

using namespace taos::search;

static const char *kUsage =
    "usage: taos-search [--json] [--timeout MS] [--limit N] \"query\"\n"
    "       taos-search menu [pinned|recent|categories|letters] [--json]     the app menu\n"
    "       taos-search pin ID | unpin ID | move-pin ID POSITION           the menu's pinned apps\n"
    "       taos-search actions [GROUP...] [--json]                        the system action menu (Capture, Toggle, Style, Setup, Install, System)\n"
    "       taos-search run ACTION [confirm]                               run one action; power actions need the word confirm\n"
    "exit status: 0 done, 1 refused (a sentence on stderr), 2 usage\n";

// The app menu and the action menu without a daemon: the same engines the launcher uses (agent parity).
static int menuCommand(const QString &cmd, QStringList args, bool json) {
    auto frec = std::make_shared<Frecency>(Frecency::defaultPath());
    auto apps = std::make_shared<AppsProvider>(frec);
    AppMenu menu(apps, frec, AppMenu::defaultPaths(), false);
    ActionMenu actions;
    const auto out = [](const QJsonValue &v) {
        fputs((v.isArray() ? QJsonDocument(v.toArray()) : QJsonDocument(v.toObject())).toJson(QJsonDocument::Indented).constData(), stdout);
    };
    const auto fail = [](const QString &m) { fprintf(stderr, "taos-search: %s\n", qPrintable(m)); return 1; };
    if (cmd == QLatin1String("menu")) {
        const QJsonObject o = menu.toJson();
        const QString part = args.value(0);
        if (!part.isEmpty() && !o.contains(part)) { fprintf(stderr, "taos-search: menu parts are pinned, recent, categories, letters\n"); return 2; }
        if (json) { out(part.isEmpty() ? QJsonValue(o) : o.value(part)); return 0; }
        const auto printApp = [](const QJsonObject &a) {
            printf("  %-34s %s%s\n", qPrintable(a.value("name").toString()), qPrintable(a.value("group").toString()),
                   a.value("pinned").toBool() ? "  [pinned]" : "");
        };
        const auto section = [&](const QString &title, const QJsonArray &arr) {
            printf("%s\n", qPrintable(title));
            for (const QJsonValue &v : arr) printApp(v.toObject());
        };
        if (part.isEmpty() || part == QLatin1String("pinned")) section(QStringLiteral("Pinned"), o.value("pinned").toArray());
        if (part.isEmpty() || part == QLatin1String("recent")) section(QStringLiteral("Recent"), o.value("recent").toArray());
        for (const QString &k : { QStringLiteral("categories"), QStringLiteral("letters") }) {
            if (!(part == k || (part.isEmpty() && k == QLatin1String("categories")))) continue;
            for (const QJsonValue &c : o.value(k).toArray()) section(c.toObject().value("title").toString(), c.toObject().value("apps").toArray());
        }
        return 0;
    }
    QString err;
    if (cmd == QLatin1String("pin") || cmd == QLatin1String("unpin")) {
        if (args.size() != 1) { fprintf(stderr, "usage: taos-search %s <desktop-id>\n", qPrintable(cmd)); return 2; }
        return (cmd == QLatin1String("pin") ? menu.pin(args[0], &err) : menu.unpin(args[0], &err)) ? 0 : fail(err);
    }
    if (cmd == QLatin1String("move-pin")) {
        bool ok = false;
        const int to = args.value(1).toInt(&ok);
        if (args.size() != 2 || !ok) { fputs("usage: taos-search move-pin <desktop-id> <position>\n", stderr); return 2; }
        return menu.movePinned(args[0], to, &err) ? 0 : fail(err);
    }
    if (cmd == QLatin1String("actions")) {
        const QList<ActionNode> l = actions.level(args);
        if (l.isEmpty() && !args.isEmpty()) return fail(QStringLiteral("no such action group: %1").arg(args.join(QLatin1Char('/'))));
        if (json) {
            QJsonArray a;
            for (const ActionNode &n : l) a.append(QJsonObject{ { "id", n.id }, { "title", n.title }, { "subtitle", n.subtitle }, { "group", n.isGroup }, { "confirm", n.action.confirm } });
            out(a);
        } else {
            for (const ActionNode &n : l) printf("%-24s %s%s\n", qPrintable(n.id), qPrintable(n.title), n.isGroup ? "  >" : n.action.confirm ? "  (asks to confirm)" : "");
        }
        return 0;
    }
    if (cmd == QLatin1String("run")) {
        const ActionNode n = actions.find(args.value(0));
        if (n.id.isEmpty() || n.isGroup) return fail(QStringLiteral("no such action: %1").arg(args.value(0)));
        if (n.action.confirm && args.value(1) != QLatin1String("confirm")) return fail(QStringLiteral("%1 needs confirmation: taos-search run %2 confirm").arg(n.title, n.id));
        Action a = n.action;
        a.confirm = false;
        return runAction(a, &err) ? 0 : fail(err.isEmpty() ? QStringLiteral("that did not work") : err);
    }
    return 2;
}

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    bool json = false;
    int timeout = 1500, limit = 30;
    QStringList words;
    const QStringList a = app.arguments().mid(1);
    static const QStringList kMenuCommands = { "menu", "pin", "unpin", "move-pin", "actions", "run" };
    if (!a.isEmpty() && kMenuCommands.contains(a.first())) {
        QStringList rest;
        bool j = false;
        for (const QString &x : a.mid(1)) { if (x == QLatin1String("--json")) j = true; else rest << x; }
        return menuCommand(a.first(), rest, j);
    }
    for (int i = 0; i < a.size(); ++i) {
        if (a[i] == QLatin1String("--json")) json = true;
        else if (a[i] == QLatin1String("--timeout") && i + 1 < a.size()) timeout = a[++i].toInt();
        else if (a[i] == QLatin1String("--limit") && i + 1 < a.size()) limit = a[++i].toInt();
        else if (a[i] == QLatin1String("--help") || a[i] == QLatin1String("-h")) {
            fputs(kUsage, stdout);
            return 0;
        } else words << a[i];
    }
    const QString text = words.join(QLatin1Char(' '));
    SearchEngine engine;
    installDefaultProviders(&engine);
    QList<Row> rows;
    QEventLoop loop;
    QObject::connect(&engine, &SearchEngine::updated, &loop, [&](int, const QList<Row> &r, bool fin) {
        rows = r;
        if (fin) loop.quit();
    });
    QTimer::singleShot(timeout, &loop, &QEventLoop::quit);
    engine.prepare();
    engine.search(text);
    loop.exec();
    if (limit > 0 && rows.size() > limit) rows = rows.mid(0, limit);
    if (json) {
        fputs(QJsonDocument(toJson(rows)).toJson(QJsonDocument::Indented).constData(), stdout);
        return 0;
    }
    QString section;
    for (const Row &r : rows) {
        if (r.section != section) { section = r.section; printf("%s\n", qPrintable(section)); }
        printf("  %-40s %s  [%s]\n", qPrintable(r.r.title), qPrintable(r.r.subtitle),
               qPrintable(r.r.actions.isEmpty() ? QString() : r.r.actions.first().label));
    }
    return 0;
}
