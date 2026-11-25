// SPDX-License-Identifier: AGPL-3.0-or-later
// Offscreen renders of the launcher for review:
//   taos-launcher-shots <out-dir> [theme] [dark|light] [style] [WIDTHxHEIGHT]
// `style` forces a launcher style (centered anchored ring text phone); empty = the theme's own. Set TAOS_THEME_DIR to the
// shell's themes folder to render the shipped Workbench and Parallax themes. Not a test: it needs no compositor, only the
// offscreen Qt platform. Fixture apps (every kind: host, taOS, app group, web) and icons come from tests/fixtures.
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QGuiApplication>
#include <QIcon>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QQuickWindow>
#include <QTimer>
#include <QtQml>

#include "actionmenu.h"
#include "appmenu.h"
#include "controller.h"
#include "glass.h"
#include "iconprovider.h"
#include "providers_more.h"
#include "theme.h"
#include "thememanager.h"

using namespace taos;

static void settle(int ms) {
    QEventLoop l;
    QTimer::singleShot(ms, &l, &QEventLoop::quit);
    l.exec();
}

int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", "offscreen");
    QGuiApplication app(argc, argv);
    const QStringList a = app.arguments();
    if (a.size() < 2) { fprintf(stderr, "usage: taos-launcher-shots <out-dir> [theme] [dark|light]\n"); return 2; }
    const QString outDir = a[1];
    const QString theme = a.value(2, QStringLiteral("graphite"));
    const QString variant = a.value(3, QStringLiteral("dark"));
    const QString styleArg = a.value(4);
    QSize size(1280, 800);
    if (a.size() > 5) { const QStringList wh = a[5].split(QLatin1Char('x')); if (wh.size() == 2) size = QSize(wh[0].toInt(), wh[1].toInt()); }
    const QString tag = QStringLiteral("%1-%2-%3").arg(styleArg.isEmpty() ? theme : styleArg, variant, a.size() > 5 ? a[5] : QStringLiteral("desktop"));
    QDir().mkpath(outDir);
    QIcon::setThemeName(QStringLiteral("Adwaita"));
    QIcon::setThemeSearchPaths(QIcon::themeSearchPaths() << QStringLiteral(TAOS_LAUNCHER_FIXTURES) + QStringLiteral("/menu-theme") << QStringLiteral(TAOS_APP_ICONS_DIR));

    Theme th;
    ThemeManager::Options o;
    o.pinnedTheme = theme;
    o.pinnedVariant = variant;
    o.watch = false;
    o.appearancePath = outDir + QStringLiteral("/.appearance-unused.json");
    ThemeManager tm(&th, o);
    QString err;
    if (!tm.start(&err)) { fprintf(stderr, "theme: %s\n", qPrintable(err)); return 1; }
    Style style(&th);

    const QString fixtures = QStringLiteral(TAOS_LAUNCHER_FIXTURES);
    // every kind of app, with their icons: a copy of the fixture entries where the web apps' icon folder is filled in
    const QString work = outDir + QStringLiteral("/.work-") + tag;
    QDir(work).removeRecursively();
    QDir().mkpath(work + QStringLiteral("/apps"));
    for (const QString &f : QDir(fixtures + QStringLiteral("/menu-apps")).entryList({ QStringLiteral("*.desktop") })) {
        QFile in(fixtures + QStringLiteral("/menu-apps/") + f);
        in.open(QIODevice::ReadOnly);
        QByteArray t = in.readAll();
        t.replace("/ICONDIR", (fixtures + QStringLiteral("/menu-icons")).toUtf8());
        QFile out(work + QStringLiteral("/apps/") + f);
        out.open(QIODevice::WriteOnly);
        out.write(t);
    }
    auto frec = std::make_shared<search::Frecency>(work + QStringLiteral("/frecency.json"));
    for (const char *id : { "vlc", "vlc", "vlc", "libreoffice-writer", "libreoffice-writer", "zeal", "audacity", "steam" }) frec->record(QStringLiteral("app:") + QString::fromLatin1(id) + QStringLiteral(".desktop"));
    {
        QFile pf(work + QStringLiteral("/startmenu.json"));
        pf.open(QIODevice::WriteOnly);
        pf.write(R"({"pinned":["firefox.desktop","foot.desktop","my.taos.messages.desktop","my.taos.agents.desktop","my.taos.store.desktop","code.desktop","gimp.desktop","taos-appgroups-blender.desktop","taos-webapp-gh1.desktop","my.taos.dashboard.desktop","my.taos.settings.desktop","org.gnome.Nautilus.desktop"]})");
    }
    auto apps = std::make_shared<search::AppsProvider>(frec, QStringList{ work + QStringLiteral("/apps") }, QStringLiteral("C"), QStringList{});
    search::SearchEngine engine;
    engine.addProvider(apps);
    engine.addProvider(std::make_shared<search::CommandsProvider>([](const QString &) { return true; }, frec));
    engine.addProvider(std::make_shared<search::CalcProvider>());
    auto cfg = std::make_shared<search::LauncherConfig>(work + QStringLiteral("/none.json"));
    engine.addProvider(std::make_shared<search::SettingsProvider>([](const QString &) { return false; }));
    auto agents = std::make_shared<search::FakeDirectory>(QList<search::Agent>{ { "a1", "Parker", "online" }, { "a2", "Quill", "busy" } });
    engine.addProvider(std::make_shared<search::MentionProvider>(agents, cfg, false));
    launcher::Controller c(&engine, frec);
    c.setSender(std::make_shared<search::FakeSender>());
    c.setLaunchDelay(0);
    c.setStyleOverride(styleArg);
    c.setMenu(std::make_shared<search::AppMenu>(apps, frec, search::AppMenu::Paths{ work + QStringLiteral("/startmenu.json"), QString() }, false),
              std::make_shared<search::ActionMenu>([](const QString &) { return true; }));
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Style", &style);
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Launcher", &c);
    QQmlEngine qml;
    qml.addImageProvider(QStringLiteral("icon"), new launcher::IconProvider());
    QQuickView view(&qml, nullptr);
    view.setColor(variant == QLatin1String("dark") ? QColor(0x2b, 0x35, 0x44) : QColor(0xb9, 0xc6, 0xd6));   // stands in for a wallpaper
    view.setResizeMode(QQuickView::SizeRootObjectToView);
    view.resize(size);
    view.setSource(QUrl(QStringLiteral("qrc:/taos/launcher/qml/Launcher.qml")));
    if (view.status() != QQuickView::Ready) {
        for (const auto &e : view.errors()) fprintf(stderr, "%s\n", qPrintable(e.toString()));
        return 1;
    }
    view.show();
    QQuickItem *root = view.rootObject();
    const auto shot = [&](const QString &name, const QString &q, const std::function<void()> &after = {}) {
        QMetaObject::invokeMethod(root, "setSearchText", Q_ARG(QVariant, q));
        settle(450);
        if (after) { after(); settle(450); }
        const QString f = outDir + QStringLiteral("/%1-%2.png").arg(name, tag);
        view.grabWindow().save(f);
        printf("%s\n", qPrintable(f));
    };
    settle(150);
    c.show();
    settle(600);
    auto *m = c.menuController();
    shot(QStringLiteral("menu-home"), QString(), [&] { m->move(0, 1); m->move(1, 0); });
    shot(QStringLiteral("menu-allapps-az"), QString(), [&] { m->setSort(QStringLiteral("az")); });
    m->setSort(QStringLiteral("category"));
    shot(QStringLiteral("menu-context"), QString(), [&] { m->openContextFor(QStringLiteral("pinned"), 7); m->move(0, 1); });
    m->closeContext();
    shot(QStringLiteral("menu-info"), QString(), [&] { m->openContextFor(QStringLiteral("pinned"), 7); m->runContext(m->contextLabels().indexOf(QStringLiteral("App info"))); });
    m->back();
    shot(QStringLiteral("menu-actions"), QStringLiteral(">"));
    shot(QStringLiteral("menu-actions-system"), QStringLiteral(">"), [&] { m->setActionIndex(5); m->enter(); });
    shot(QStringLiteral("menu-search"), QStringLiteral("fire"));
    shot(QStringLiteral("menu-search-calc"), QStringLiteral("12 * (3 + 4) / 2"));
    shot(QStringLiteral("menu-power"), QString(), [&] { m->openPower(); m->runPower(3); });
    return 0;
}
