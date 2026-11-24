// SPDX-License-Identifier: AGPL-3.0-or-later
// Renders every first-cut page offscreen, in graphite dark and light, against fake engines and temp folders.
//   settings_shots <outdir>
#include <QDir>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QtQml/qqml.h>
#include <QTest>

#include "taosqt/appcontext.h"
#include "taosqt/qmlregister.h"
#include "taosqt/session.h"

#include "rig.h"
#include "storeengine.h"

int main(int argc, char *argv[])
{
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("QT_QUICK_BACKEND", "software");
    QGuiApplication app(argc, argv);
    const QString out = argc > 1 ? QString::fromLocal8Bit(argv[1]) : QDir::currentPath();
    QDir().mkpath(out);

    Rig rig(QStringLiteral("two.json"));
    // Something to look at on the pages that depend on live state.
    {
        QFile f(rig.path("config/taos/dock.json"));
        f.open(QIODevice::WriteOnly);
        f.write("{\"position\":\"bottom\",\"autohide\":true,\"magnify\":true,\"size\":\"medium\"}");
        QFile d(rig.path("config/taos/desktop.json"));
        d.open(QIODevice::WriteOnly);
        d.write("{\"default\":\"desktop\",\"workspaces\":{\"2\":\"tiling\"}}");
    }
    QTest::qWait(400);

    Taos::Session session{Taos::ClientOptions()};
    Taos::registerQmlTypes();
    Taos::Store::registerStoreQmlTypes();
    Taos::AppContext context(&session, QStringLiteral("my.taos.settings"));
    context.setStyle(rig.ctx->style());
    QQmlApplicationEngine engine;
    context.setEngine(&engine);
    qmlRegisterSingletonInstance("Taos", 1, 0, "Taos", &context);
    qmlRegisterSingletonInstance("Taos.Settings", 1, 0, "Settings", rig.ctx);
    engine.addImportPath(QStringLiteral("qrc:/taos/qml"));
    engine.load(QUrl(QStringLiteral("qrc:/my.taos.settings/Main.qml")));
    if (engine.rootObjects().isEmpty()) return 1;
    auto *w = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    w->resize(900, 640);
    w->show();
    QTest::qWait(500);

    int failures = 0;
    auto shot = [&](const QString &name) {
        QTest::qWait(450);
        const QImage img = w->grabWindow();
        if (img.isNull() || !img.save(out + QLatin1Char('/') + name + QStringLiteral(".png"))) { qWarning("cannot save %s", qPrintable(name)); ++failures; }
    };
    const QStringList sections = {QStringLiteral("appearance"), QStringLiteral("display"), QStringLiteral("dock"), QStringLiteral("desktop"),
                                  QStringLiteral("sound"), QStringLiteral("network"), QStringLiteral("apps"), QStringLiteral("about")};
    for (const QString &mode : {QStringLiteral("dark"), QStringLiteral("light")}) {
        rig.ctx->themes()->choose(QStringLiteral("graphite"), mode);
        for (const QString &s : sections) {
            rig.ctx->nav()->navigate(s);
            shot(s + QLatin1Char('-') + mode);
        }
        rig.ctx->nav()->navigate(QStringLiteral("dock"), QStringLiteral("dock.autohide"));
        shot(QStringLiteral("deeplink-dock-autohide-") + mode);
        rig.ctx->nav()->setQuery(QStringLiteral("zoom"));
        shot(QStringLiteral("search-zoom-") + mode);
        rig.ctx->nav()->setQuery(QStringLiteral("qqq"));
        shot(QStringLiteral("search-empty-") + mode);
        rig.ctx->nav()->setQuery(QString());
        // the 10 second "Keep this setting?" bar
        rig.ctx->nav()->navigate(QStringLiteral("display"));
        DisplayController *c = rig.ctx->display()->controller();
        const QVariantMap o = c->outputs().first().toMap();
        for (const QVariant &v : o.value(QStringLiteral("rates")).toList())
            if (!v.toMap().value(QStringLiteral("current")).toBool()) { c->setRefresh(o.value(QStringLiteral("name")).toString(), v.toMap().value(QStringLiteral("mHz")).toInt()); break; }
        shot(QStringLiteral("display-keep-") + mode);
        c->revert();
    }
    rig.ctx->themes()->choose(QStringLiteral("graphite-ice"), QStringLiteral("dark"));
    rig.ctx->nav()->navigate(QStringLiteral("appearance"));
    shot(QStringLiteral("appearance-graphite-ice-dark"));
    rig.ctx->themes()->choose(QStringLiteral("graphite-warm"), QStringLiteral("light"));
    shot(QStringLiteral("appearance-graphite-warm-light"));
    return failures ? 2 : 0;
}