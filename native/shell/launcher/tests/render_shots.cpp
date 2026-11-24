// SPDX-License-Identifier: AGPL-3.0-or-later
// Offscreen renders of the launcher for review: taos-launcher-shots <out-dir> [theme] [variant]
// Not a test: it needs no compositor, only the offscreen Qt platform.
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
    QDir().mkpath(outDir);
    QIcon::setThemeName(QStringLiteral("Adwaita"));

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
    QFile wf(fixtures + QStringLiteral("/windows.json"));
    wf.open(QIODevice::ReadOnly);
    const QByteArray windows = wf.readAll();

    const QString frecPath = outDir + QStringLiteral("/.frecency.json");
    QFile::remove(frecPath);
    auto frec = std::make_shared<search::Frecency>(frecPath);
    frec->record(QStringLiteral("app:firefox.desktop"));
    frec->record(QStringLiteral("app:firefox.desktop"));
    frec->record(QStringLiteral("app:foot.desktop"));
    frec->record(QStringLiteral("app:org.taos.Files.desktop"));
    // an app whose Icon= is an absolute path to a PNG (the encoded image:// id must decode to it)
    QDir extra(outDir + QStringLiteral("/.apps"));
    extra.removeRecursively();
    extra.mkpath(QStringLiteral("."));
    {
        QImage png(64, 64, QImage::Format_ARGB32);
        png.fill(QColor(0x2f, 0x9e, 0x6f));
        png.save(outDir + QStringLiteral("/.apps/glyph.png"));
        QFile d(outDir + QStringLiteral("/.apps/glyph.desktop"));
        d.open(QIODevice::WriteOnly);
        d.write(("[Desktop Entry]\nType=Application\nName=Glyph Pad\nIcon=" + outDir + "/.apps/glyph.png\nExec=glyph\n").toUtf8());
    }
    auto apps = std::make_shared<search::AppsProvider>(frec, QStringList{ fixtures + QStringLiteral("/applications"), outDir + QStringLiteral("/.apps") }, QStringLiteral("C"), QStringList{});
    search::SearchEngine engine;
    engine.addProvider(apps);
    engine.addProvider(std::make_shared<search::WindowsProvider>([windows](bool *ok) { *ok = true; return windows; },
                                                                [apps](const QString &id) { return apps->iconForAppId(id); }));
    engine.addProvider(std::make_shared<search::CommandsProvider>([](const QString &) { return true; }, frec));
    engine.addProvider(std::make_shared<search::CalcProvider>());
    auto cfg = std::make_shared<search::LauncherConfig>(outDir + QStringLiteral("/.none.json"));
    QDir home(outDir + QStringLiteral("/.home"));
    home.removeRecursively();
    home.mkpath(QStringLiteral("Documents"));
    for (const char *f : { "Documents/Quarterly report.pdf", "Documents/report-draft.odt", "Documents/Trip report notes.txt" }) QFile(home.filePath(QString::fromUtf8(f))).open(QIODevice::WriteOnly);
    auto idx = std::make_shared<search::FileIndex>(QStringList{ home.path() }, outDir + QStringLiteral("/.files.txt"));
    idx->rebuild(search::CancelToken());
    engine.addProvider(std::make_shared<search::FilesProvider>(idx));
    engine.addProvider(std::make_shared<search::SettingsProvider>([](const QString &) { return false; }));
    auto agents = std::make_shared<search::FakeDirectory>(QList<search::Agent>{ { "a1", "Parker", "online" }, { "a2", "Quill", "busy" }, { "a3", "Pixel Pete", "offline" } });
    engine.addProvider(std::make_shared<search::MentionProvider>(agents, cfg, false));
    engine.addProvider(std::make_shared<search::AskProvider>(cfg, [] { return QList<search::DesktopEntry>(); }));
    launcher::Controller c(&engine, frec);
    c.setSender(std::make_shared<search::FakeSender>());
    c.setLaunchDelay(0);
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Style", &style);
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Launcher", &c);
    QQmlEngine qml;
    qml.addImageProvider(QStringLiteral("icon"), new launcher::IconProvider());
    QQuickView view(&qml, nullptr);
    view.setColor(variant == QLatin1String("dark") ? QColor(0x2b, 0x35, 0x44) : QColor(0xb9, 0xc6, 0xd6));   // stands in for a wallpaper
    view.setResizeMode(QQuickView::SizeRootObjectToView);
    view.resize(1280, 800);
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
        const QString f = outDir + QStringLiteral("/%1-%2-%3.png").arg(name, theme, variant);
        view.grabWindow().save(f);
        printf("%s\n", qPrintable(f));
    };
    settle(100);
    c.show();
    settle(500);
    shot(QStringLiteral("empty"), QString());
    shot(QStringLiteral("app-query"), QStringLiteral("fire"));
    shot(QStringLiteral("mixed"), QStringLiteral("t"));
    shot(QStringLiteral("calculator"), QStringLiteral("12 * (3 + 4) / 2"));
    shot(QStringLiteral("actions"), QStringLiteral("fire"), [&] { c.openActions(); c.moveAction(1); });
    shot(QStringLiteral("abs-icon"), QStringLiteral("glyph"));
    shot(QStringLiteral("none"), QStringLiteral("zzqxv"));
    shot(QStringLiteral("files-settings"), QStringLiteral("report"));
    shot(QStringLiteral("settings"), QStringLiteral("display"));
    shot(QStringLiteral("mention-complete"), QStringLiteral("@p"));
    shot(QStringLiteral("mention-send"), QStringLiteral("@parker can you summarise my inbox?"));
    shot(QStringLiteral("power"), QStringLiteral("shut"), [&] { c.activate(); });
    return 0;
}
