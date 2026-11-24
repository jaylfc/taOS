// taos-search: the launcher's search engine without the launcher. For a TUI over SSH, scripts and agents.
//   taos-search [--json] [--timeout MS] [--limit N] "query"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QJsonDocument>
#include <QTimer>
#include <cstdio>

#include "engine.h"
#include "jsonio.h"
#include "setup.h"

using namespace taos::search;

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    bool json = false;
    int timeout = 1500, limit = 30;
    QStringList words;
    const QStringList a = app.arguments().mid(1);
    for (int i = 0; i < a.size(); ++i) {
        if (a[i] == QLatin1String("--json")) json = true;
        else if (a[i] == QLatin1String("--timeout") && i + 1 < a.size()) timeout = a[++i].toInt();
        else if (a[i] == QLatin1String("--limit") && i + 1 < a.size()) limit = a[++i].toInt();
        else if (a[i] == QLatin1String("--help") || a[i] == QLatin1String("-h")) {
            fputs("usage: taos-search [--json] [--timeout MS] [--limit N] \"query\"\n", stdout);
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
