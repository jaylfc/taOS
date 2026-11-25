// SPDX-License-Identifier: AGPL-3.0-or-later
// Dev tool: renders the dock (and its menu or settings popover) offscreen with fixture windows.
//   dockshot out.png [--scene dock|menu|taskbar-menu|settings] [--position bottom|left|right] [--attached] [--size small|medium|large]
//            [--light] [--theme id] [--magnify] [--size-px WxH]
//            [--app key]            the menu of this dock item (default: Activity), found by key, not by position
//            [--windows single|several]   Activity with one window or with two (default several)
//            [--sub]                open the "Move to space" submenu beside its row
//   taskbar-menu: the Workbench taskbar (use --theme workbench) and the menu of the Activity window button
#include <QGuiApplication>
#include <QIcon>
#include <QImage>
#include <QQmlContext>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QTemporaryDir>
#include <QTimer>
#include <QtQml>

#include "dockcontroller.h"
#include "iconprovider.h"
#include "theme.h"
#include "thememanager.h"

static WindowInfo win(int id, const QString &app, const QString &title, bool focused = false, bool min = false) {
    WindowInfo w;
    w.id = id; w.appId = app; w.title = title; w.focused = focused; w.minimised = min; w.workspace = QStringLiteral("1"); w.pid = 100 + id;
    w.floating = true;
    return w;
}

int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", qgetenv("QT_QPA_PLATFORM").isEmpty() ? QByteArray("offscreen") : qgetenv("QT_QPA_PLATFORM"));
    if (qEnvironmentVariableIsEmpty("QT_QUICK_BACKEND")) qputenv("QT_QUICK_BACKEND", "software");
    QGuiApplication app(argc, argv);
    const QStringList a = app.arguments();
    if (a.size() < 2) { fprintf(stderr, "usage: dockshot out.png [options]\n"); return 2; }
    auto opt = [&](const char *k, const QString &def) { const int i = a.indexOf(QLatin1String(k)); return i > 0 && i + 1 < a.size() ? a.at(i + 1) : def; };
    const QString scene = opt("--scene", QStringLiteral("dock"));
    QSize size(1280, 720);
    if (const auto p = opt("--size-px", QString()).split(QLatin1Char('x')); p.size() == 2) size = QSize(p[0].toInt(), p[1].toInt());

    QTemporaryDir tmp;
    QIcon::setThemeSearchPaths({QStringLiteral(TAOS_DOCK_ICONS)});
    QIcon::setThemeName(QStringLiteral("hicolor"));

    Theme theme;
    ThemeManager::Options to;
    to.pinnedTheme = opt("--theme", QStringLiteral("graphite"));
    to.pinnedVariant = a.contains(QLatin1String("--light")) ? QStringLiteral("light") : QStringLiteral("dark");
    to.watch = false;
    to.transitionMs = 0;
    to.appearancePath = tmp.path() + QStringLiteral("/appearance.json");
    ThemeManager themes(&theme, to);
    QString err;
    if (!themes.start(&err)) { fprintf(stderr, "theme: %s\n", qPrintable(err)); return 1; }

    DockConfig cfg;
    cfg.position = opt("--position", QStringLiteral("bottom"));
    cfg.attached = a.contains(QLatin1String("--attached"));
    cfg.size = opt("--size", QStringLiteral("medium"));
    cfg.magnify = a.contains(QLatin1String("--magnify"));
    DockConfigFile::write(tmp.path() + QStringLiteral("/dock.json"), cfg);

    DockController::Options o;
    o.configPath = tmp.path() + QStringLiteral("/dock.json");
    o.modePath = tmp.path() + QStringLiteral("/desktop.json");
    o.appDirs = {QStringLiteral(TAOS_DOCK_FIXTURES "/applications")};
    o.watch = false;
    o.swaySocket = tmp.path() + QStringLiteral("/none");
    o.engine = QStringLiteral("sway");   // never the installed taos-window: a screenshot tool must not reach any compositor
    DockController dock(o);
    // Activity with two windows (one focused), Terminal, Notifications, a minimised Browser and a
    // minimised Terminal window, and a window no .desktop entry matches.
    const bool several = opt("--windows", QStringLiteral("several")) != QLatin1String("single");
    QVector<WindowInfo> wins = {win(1, QStringLiteral("my.taos.dashboard"), QStringLiteral("Activity"), true),
                              win(2, QStringLiteral("my.taos.dashboard"), QStringLiteral("Activity: Models")),
                              win(3, QStringLiteral("foot"), QStringLiteral("alex@host: ~")),
                              win(4, QStringLiteral("my.taos.notifications"), QStringLiteral("Notifications")),
                              win(5, QStringLiteral("chromium"), QStringLiteral("taOS docs - Chromium"), false, true),
                              win(6, QStringLiteral("foot"), QStringLiteral("build log"), false, true),
                              win(7, QStringLiteral("unknown-app"), QStringLiteral("Unmatched window"))};
    if (!several) wins.removeAt(1);
    dock.model()->setWindows(wins);
    dock.taskbar()->setWindows(wins);
    dock.model()->setSpaces({{QStringLiteral("1"), true, true, 3}, {QStringLiteral("2"), false, false, 1}, {QStringLiteral("3"), false, false, 0}});
    dock.setOutputSize(size);

    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Theme", &theme);
    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Dock", &dock);
    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "DockSettings", dock.settings());
    QQmlEngine engine;
    engine.addImageProvider(QStringLiteral("dockicon"), new DockIconProvider);
    QQuickView v(&engine, nullptr);
    v.setColor(Qt::black);
    v.setResizeMode(QQuickView::SizeRootObjectToView);
    v.resize(size);
    const Layout &l = dock.layout();
    const QPoint pos = l.surfacePos(size);
    QPointF anchor = QPointF(l.strip.center());
    int subRow = -1;
    if (scene == QLatin1String("taskbar-menu")) {
        anchor = QPointF(size.width() / 2.0 - 60, size.height() - theme.taskbarHeight() / 2.0);
        dock.taskbarMenu(1, anchor.x(), anchor.y());
        const QVariantList rows = dock.menuRows();
        for (int i = 0; i < rows.size(); ++i) if (rows[i].toMap().value(QStringLiteral("id")).toString().startsWith(QLatin1String("window-spaces:"))) subRow = i;
    }
    if (scene == QLatin1String("menu")) {
        const int row = dock.model()->rowForKey(opt("--app", QStringLiteral("my.taos.dashboard")));
        const qreal along = l.padding + dock.cell() * (row + 0.5);
        anchor = l.vertical ? QPointF(l.strip.center().x(), l.strip.y() + along) : QPointF(l.strip.x() + along, l.strip.center().y());
        dock.openMenu(row, anchor.x(), anchor.y());
        const QVariantList rows = dock.menuRows();
        for (int i = 0; i < rows.size(); ++i) {
            const QString id = rows[i].toMap().value(QStringLiteral("id")).toString();
            if (id.startsWith(QLatin1String("window-spaces:")) || (several && id == QLatin1String("window:1"))) subRow = i;   // several windows: the first window's controls
        }
    }
    v.setInitialProperties({{QStringLiteral("overlay"), scene == QLatin1String("dock") ? QString() : scene},
                            {QStringLiteral("subRow"), a.contains(QLatin1String("--sub")) ? subRow : -1},
                            {QStringLiteral("sx"), pos.x()}, {QStringLiteral("sy"), pos.y()},
                            {QStringLiteral("sw"), l.surface.width()}, {QStringLiteral("sh"), l.surface.height()},
                            {QStringLiteral("ax"), (scene == QLatin1String("taskbar-menu") ? 0 : pos.x()) + anchor.x()},
                            {QStringLiteral("ay"), (scene == QLatin1String("taskbar-menu") ? 0 : pos.y()) + anchor.y()}});
    v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_DOCK_TESTS_DIR "/scene.qml")));
    if (v.status() != QQuickView::Ready) {
        for (const auto &e : v.errors()) fprintf(stderr, "qml: %s\n", qPrintable(e.toString()));
        return 1;
    }
    v.show();
    QTimer::singleShot(900, [&] {
        const QImage img = v.grabWindow();
        if (!img.save(a.at(1))) { fprintf(stderr, "cannot save %s\n", qPrintable(a.at(1))); app.exit(1); return; }
        printf("saved %s (%dx%d)\n", qPrintable(a.at(1)), img.width(), img.height());
        app.quit();
    });
    return app.exec();
}
