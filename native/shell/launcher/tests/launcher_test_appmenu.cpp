// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QJsonArray>
#include <QJsonDocument>
#include <QtTest>

#include "appmenu.h"

using namespace taos::search;

static QString fixtures() { return QStringLiteral(TAOS_LAUNCHER_FIXTURES); }
static QStringList names(const QList<MenuApp> &l) { QStringList n; for (const MenuApp &a : l) n << a.name; return n; }
static QStringList ids(const QList<MenuApp> &l) { QStringList n; for (const MenuApp &a : l) n << a.id; return n; }

class TestAppMenu : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    QString m_apps, m_pins, m_dock;
    int m_n = 0;
    std::shared_ptr<Frecency> m_frec;
    std::shared_ptr<AppsProvider> m_prov;

    void copyFixtures() {
        QDir().mkpath(m_apps);
        const QString icons = m_tmp.path() + "/icons";
        QDir().mkpath(icons);
        for (const QString &f : QDir(fixtures() + "/menu-icons").entryList({ "*.svg" })) QFile::copy(fixtures() + "/menu-icons/" + f, icons + "/" + f);
        for (const QString &f : QDir(fixtures() + "/menu-apps").entryList({ "*.desktop" })) {
            QFile in(fixtures() + "/menu-apps/" + f);
            in.open(QIODevice::ReadOnly);
            QByteArray t = in.readAll();
            t.replace("/ICONDIR", icons.toUtf8());
            QFile out(m_apps + "/" + f);
            out.open(QIODevice::WriteOnly);
            out.write(t);
        }
    }
    void put(const QString &name, const QString &text, const QString &dir = {}) {
        const QString d = dir.isEmpty() ? m_apps : dir;
        QDir().mkpath(d);
        QSaveFile f(d + "/" + name);   // atomic rename, as the installers do
        f.open(QIODevice::WriteOnly);
        f.write(text.toUtf8());
        f.commit();
    }
    std::unique_ptr<AppMenu> menu(bool watch = false) {
        m_prov = std::make_shared<AppsProvider>(m_frec, QStringList{ m_apps }, "C", QStringList{});
        return std::make_unique<AppMenu>(m_prov, m_frec, AppMenu::Paths{ m_pins, m_dock }, watch);
    }
    QStringList pinsOnDisk() {
        QFile f(m_pins);
        if (!f.open(QIODevice::ReadOnly)) return { "<none>" };
        QStringList l;
        for (const QJsonValue &v : QJsonDocument::fromJson(f.readAll()).object().value("pinned").toArray()) l << v.toString();
        return l;
    }
private slots:
    void init() {
        QDir(m_tmp.path()).removeRecursively();
        QDir().mkpath(m_tmp.path());
        m_apps = m_tmp.path() + "/apps";
        m_pins = m_tmp.path() + "/cfg/startmenu.json";
        m_dock = m_tmp.path() + "/cfg/dock.json";
        m_frec = std::make_shared<Frecency>(m_tmp.path() + QString("/f%1.json").arg(m_n++));
        copyFixtures();
    }

    void parsesTaosKeys() {
        const DesktopEntry e = parseDesktopEntry("[Desktop Entry]\nType=Application\nName=B\nExec=b\nX-taOS-AppId=blender\nX-taOS-ContainerApp=blender\nX-taOS-AppGroup=My apps\nX-taOS-WebApp=w1\n", "b.desktop", "/x", "C");
        QCOMPARE(e.taosAppId, QString("blender"));
        QCOMPARE(e.containerApp, QString("blender"));
        QCOMPARE(e.appGroup, QString("My apps"));
        QCOMPARE(e.webApp, QString("w1"));
    }

    void categoryList() {
        QCOMPARE(menuCategories(), (QStringList{ "Internet", "Development", "Office", "Graphics", "Media", "Games", "System", "taOS", "Other" }));
    }
    void categoryMapping_data() {
        QTest::addColumn<QString>("id");
        QTest::addColumn<QString>("cats");
        QTest::addColumn<QString>("want");
        QTest::newRow("browser") << "firefox.desktop" << "Network;WebBrowser;" << "Internet";
        QTest::newRow("ide") << "code.desktop" << "Development;IDE;" << "Development";
        QTest::newRow("writer") << "w.desktop" << "Office;WordProcessor;" << "Office";
        QTest::newRow("gimp") << "gimp.desktop" << "Graphics;2DGraphics;" << "Graphics";
        QTest::newRow("video") << "vlc.desktop" << "AudioVideo;Player;Video;" << "Media";
        QTest::newRow("audio") << "a.desktop" << "Audio;" << "Media";
        QTest::newRow("game") << "g.desktop" << "Game;BoardGame;" << "Games";
        QTest::newRow("terminal") << "foot.desktop" << "System;TerminalEmulator;" << "System";
        QTest::newRow("files") << "n.desktop" << "Utility;Core;FileManager;" << "System";
        QTest::newRow("settings") << "s.desktop" << "Settings;" << "System";
        QTest::newRow("unknown") << "o.desktop" << "Qt;X-Foo;" << "Other";
        QTest::newRow("none") << "o2.desktop" << "" << "Other";
        QTest::newRow("utility") << "u.desktop" << "Utility;Calculator;" << "Other";
        QTest::newRow("mixed first wins") << "m.desktop" << "Network;Development;" << "Internet";
        QTest::newRow("builtin by id") << "my.taos.messages.desktop" << "Network;InstantMessaging;" << "taOS";
        QTest::newRow("builtin settings") << "my.taos.settings.desktop" << "Settings;System;" << "taOS";
    }
    void categoryMapping() {
        QFETCH(QString, id); QFETCH(QString, cats); QFETCH(QString, want);
        DesktopEntry e; e.id = id; e.categories = cats.split(';', Qt::SkipEmptyParts);
        QCOMPARE(menuCategoryFor(e), want);
    }

    void allIsSortedAtoZIgnoringCase() {
        auto m = menu();
        const QStringList n = names(m->all());
        QVERIFY2(n.size() >= 28, qPrintable(QString::number(n.size())));
        QStringList sorted = n;
        QCollator c; c.setCaseSensitivity(Qt::CaseInsensitive); c.setNumericMode(true);
        std::sort(sorted.begin(), sorted.end(), [&](const QString &a, const QString &b) { return c.compare(a, b) < 0; });
        QCOMPARE(n, sorted);
        QVERIFY(!n.contains("Hidden Thing"));       // NoDisplay never listed
        QVERIFY(n.indexOf("htop") > n.indexOf("GParted"));
        QVERIFY(n.indexOf("htop") < n.indexOf("Inkscape"));
    }
    void byCategoryOrderAndContent() {
        auto m = menu();
        const QList<MenuSection> s = m->byCategory();
        QStringList titles; for (const MenuSection &x : s) titles << x.title;
        QCOMPARE(titles, (QStringList{ "Internet", "Development", "Office", "Graphics", "Media", "Games", "System", "taOS", "Other" }));
        QMap<QString, QStringList> by; for (const MenuSection &x : s) by[x.title] = names(x.apps);
        QVERIFY(by["Internet"].contains("Firefox") && by["Internet"].contains("GitHub") && by["Internet"].contains("Thunderbird"));
        QVERIFY(by["Graphics"].contains("Blender") && by["Graphics"].contains("GIMP"));
        QVERIFY(by["Media"].contains("OBS Studio"));
        QVERIFY(by["taOS"].contains("Settings") && by["taOS"].contains("Store") && by["taOS"].contains("Messages"));
        QVERIFY(!by["Internet"].contains("Messages"));          // built in, so under taOS only
        QVERIFY(by["Other"].contains("Odd Tool"));
        QCOMPARE(by["Graphics"], by["Graphics"].mid(0));        // A-Z inside a section
        QVERIFY(by["Graphics"].indexOf("Blender") < by["Graphics"].indexOf("GIMP"));
    }
    void emptyCategoriesAreOmitted() {
        QFile::remove(m_apps + "/steam.desktop"); QFile::remove(m_apps + "/org.gnome.Chess.desktop");
        auto m = menu();
        for (const MenuSection &x : m->byCategory()) QVERIFY2(x.title != "Games", "Games must vanish when empty");
    }
    void byLetterGroups() {
        auto m = menu();
        const QList<MenuSection> s = m->byLetter();
        QVERIFY(!s.isEmpty());
        QCOMPARE(s.first().title, QString("A"));
        QMap<QString, QStringList> by; for (const MenuSection &x : s) by[x.title] = names(x.apps);
        QVERIFY(by["F"].contains("Files") && by["F"].contains("Firefox") && by["F"].contains("Fastmail (work)"));
        QVERIFY(by["H"].contains("htop"));      // lower case names sort with their letter
        QStringList letters; for (const MenuSection &x : s) letters << x.title;
        QStringList sorted = letters; std::sort(sorted.begin(), sorted.end());
        QCOMPARE(letters, sorted);
    }
    void kindsAndGroups() {
        auto m = menu();
        const MenuApp b = m->find("taos-appgroups-blender");
        QVERIFY(b.valid());
        QCOMPARE(b.kind, QString("group"));
        QCOMPARE(b.group, QString("My apps"));
        QCOMPARE(b.containerApp, QString("blender"));
        QCOMPARE(m->find("taos-appgroups-obs.desktop").group, QString("Streaming"));
        QCOMPARE(m->find("taos-webapp-gh1").kind, QString("web"));
        QCOMPARE(m->find("my.taos.store").kind, QString("taos"));
        QCOMPARE(m->find("firefox").kind, QString("app"));
        QVERIFY(m->find("firefox").group.isEmpty());
        QVERIFY(!m->find("nope").valid());
        QCOMPARE(m->find("htop").terminal, true);
        QCOMPARE(m->find("firefox").argv, QStringList{ "firefox" });
    }

    // ---- pins
    void pinsStartFromTheDocksList() {
        QDir().mkpath(m_tmp.path() + "/cfg");
        QFile d(m_dock); d.open(QIODevice::WriteOnly);
        d.write(R"({"position":"bottom","pinned":["my.taos.settings","firefox","gone-app","foot"]})");
        d.close();
        auto m = menu();
        QCOMPARE(names(m->pinned()), (QStringList{ "Settings", "Firefox", "Terminal" }));   // not installed: skipped, never an error
        QVERIFY(m->isPinned("firefox"));
        QVERIFY(m->isPinned("firefox.desktop"));
        QVERIFY(m->find("firefox").pinned);
    }
    void pinsDefaultWhenNeitherFileExists() {
        auto m = menu();
        const QStringList p = names(m->pinned());
        QVERIFY(p.contains("Settings"));
        QVERIFY(p.contains("Terminal"));
        QVERIFY(p.contains("Firefox"));
        QVERIFY(p.size() >= 5);
    }
    void pinWritesItsOwnListAndKeepsTheDocksFile() {
        QDir().mkpath(m_tmp.path() + "/cfg");
        QFile d(m_dock); d.open(QIODevice::WriteOnly); d.write(R"({"pinned":["firefox"]})"); d.close();
        auto m = menu();
        QString err;
        QVERIFY2(m->pin("gimp", &err), qPrintable(err));
        QCOMPARE(pinsOnDisk(), (QStringList{ "firefox.desktop", "gimp.desktop" }));
        QVERIFY(m->isPinned("gimp"));
        QVERIFY(m->pin("gimp", &err));      // pinning twice is fine, no duplicate
        QCOMPARE(pinsOnDisk().size(), 2);
        QVERIFY(m->unpin("firefox", &err));
        QCOMPARE(pinsOnDisk(), (QStringList{ "gimp.desktop" }));
        QFile dk(m_dock); dk.open(QIODevice::ReadOnly);
        QCOMPARE(dk.readAll(), QByteArray(R"({"pinned":["firefox"]})"));   // the dock's own list is never touched
        auto again = menu();    // survives a restart
        QCOMPARE(names(again->pinned()), (QStringList{ "GIMP" }));
    }
    void pinRefusesWhatIsNotInstalled() {
        auto m = menu();
        QString err;
        QVERIFY(!m->pin("nope", &err));
        QVERIFY(err.contains("nope"));
        QVERIFY(!QFileInfo::exists(m_pins));     // a refused pin writes nothing
    }
    void unpinOfAnUnpinnedAppIsAnError() {
        auto m = menu();
        QString err;
        m->pin("gimp"); 
        QVERIFY(!m->unpin("vlc", &err));
        QVERIFY(!err.isEmpty());
    }
    void movePinnedReorders() {
        auto m = menu();
        m->pin("gimp"); m->pin("vlc"); m->pin("steam");
        const QStringList before = ids(m->pinned());
        QVERIFY(before.indexOf("steam.desktop") > before.indexOf("gimp.desktop"));
        QString err;
        QVERIFY2(m->movePinned("steam", 0, &err), qPrintable(err));
        QCOMPARE(ids(m->pinned()).first(), QString("steam.desktop"));
        QVERIFY(!m->movePinned("steam", 99, &err));
        QVERIFY(!m->movePinned("nope", 0, &err));
    }
    void aBrokenPinsFileFallsBackAndIsNotOverwrittenBlindly() {
        QDir().mkpath(m_tmp.path() + "/cfg");
        QFile f(m_pins); f.open(QIODevice::WriteOnly); f.write("{not json"); f.close();
        auto m = menu();
        QVERIFY(m->pinned().size() >= 1);       // defaults, not an empty menu
    }

    // ---- recent
    void recentIsFrecentAndNeverPinned() {
        for (int i = 0; i < 5; ++i) m_frec->record("app:vlc.desktop");
        for (int i = 0; i < 3; ++i) m_frec->record("app:gimp.desktop");
        m_frec->record("app:steam.desktop");
        m_frec->record("app:gone.desktop");
        auto m = menu();
        m->pin("vlc");
        const QStringList r = ids(m->recent(6));
        QCOMPARE(r, (QStringList{ "gimp.desktop", "steam.desktop" }));
        QCOMPARE(m->recent(1).size(), 1);
    }

    // ---- live updates
    void liveInstallRemoveAndRename() {
        auto m = menu(true);
        QVERIFY(m->count() > 0);
        QSignalSpy spy(m.get(), &AppMenu::changed);
        put("newapp.desktop", "[Desktop Entry]\nType=Application\nName=Aardvark\nExec=aard\nCategories=Game;\n");
        QTRY_VERIFY_WITH_TIMEOUT(spy.count() >= 1, 3000);
        QCOMPARE(names(m->all()).first(), QString("Aardvark"));
        spy.clear();
        QFile::remove(m_apps + "/newapp.desktop");
        QTRY_VERIFY_WITH_TIMEOUT(spy.count() >= 1, 3000);
        QVERIFY(!names(m->all()).contains("Aardvark"));
        // an installer that writes a new app into a folder that did not exist yet
        spy.clear();
        put("deep.desktop", "[Desktop Entry]\nType=Application\nName=Zebra Deep\nExec=z\n", m_apps + "/sub/deeper");
        QTRY_VERIFY_WITH_TIMEOUT(spy.count() >= 1, 3000);
        QVERIFY(names(m->all()).contains("Zebra Deep"));
        // an edit in place (the name changes) is seen too, and a burst is one change, not fifty
        spy.clear();
        for (int i = 0; i < 20; ++i) put("burst.desktop", QString("[Desktop Entry]\nType=Application\nName=Burst %1\nExec=b\n").arg(i));
        QTRY_VERIFY_WITH_TIMEOUT(names(m->all()).contains("Burst 19"), 3000);
        QVERIFY(spy.count() <= 4);
    }
    void liveRemovalDropsPins() {
        auto m = menu(true);
        m->pin("gimp");
        QVERIFY(!m->pinned().isEmpty());
        QSignalSpy spy(m.get(), &AppMenu::changed);
        QFile::remove(m_apps + "/gimp.desktop");
        QTRY_VERIFY_WITH_TIMEOUT(spy.count() >= 1, 3000);
        QVERIFY(!names(m->pinned()).contains("GIMP"));
        QVERIFY(pinsOnDisk().contains("gimp.desktop"));     // the pin is kept: a reinstall brings it back
        put("gimp.desktop", "[Desktop Entry]\nType=Application\nName=GIMP\nExec=gimp\nCategories=Graphics;\n");
        QTRY_VERIFY_WITH_TIMEOUT(names(m->pinned()).contains("GIMP"), 3000);
    }
    void pinsFileChangedByAnotherProcessIsSeen() {
        auto m = menu(true);
        QSignalSpy spy(m.get(), &AppMenu::changed);
        auto other = menu(false);
        QVERIFY(other->pin("vlc"));      // `taos-search pin` from a terminal
        QTRY_VERIFY_WITH_TIMEOUT(spy.count() >= 1, 3000);
        QVERIFY(m->isPinned("vlc"));
    }

    // ---- what can be done with an app
    void uninstallRoutesByKind() {
        auto m = menu();
        QCOMPARE(m->uninstallArgv(m->find("taos-appgroups-blender")), (QStringList{ "taos-appgroups", "remove", "blender" }));
        QCOMPARE(m->uninstallArgv(m->find("taos-webapp-gh1")), (QStringList{ "taos-webapp", "remove", "gh1" }));
        QVERIFY(m->uninstallArgv(m->find("firefox")).isEmpty());        // host packages are the package manager's
        QVERIFY(m->uninstallArgv(m->find("my.taos.settings")).isEmpty());
    }
    void contextActionsPerKind() {
        auto m = menu();
        const auto labels = [&](const QString &id) { QStringList l; for (const Action &a : m->contextActions(m->find(id))) l << a.id; return l; };
        QCOMPARE(labels("firefox").first(), QString("open"));
        QVERIFY(labels("vlc").contains("pin"));          // vlc is not one of the starting pins
        QVERIFY(labels("firefox").contains("unpin"));    // firefox is
        QVERIFY(labels("firefox").contains("info"));
        QVERIFY(!labels("firefox").contains("uninstall"));
        QVERIFY(!labels("firefox").contains("move-group"));
        m->pin("vlc");
        QVERIFY(labels("vlc").contains("unpin"));
        QVERIFY(!labels("vlc").contains("pin"));
        QVERIFY(labels("taos-appgroups-blender").contains("uninstall"));
        QVERIFY(labels("taos-appgroups-blender").contains("move-group"));
        QVERIFY(labels("taos-webapp-gh1").contains("uninstall"));
        QVERIFY(!labels("taos-webapp-gh1").contains("move-group"));
        for (const Action &a : m->contextActions(m->find("taos-appgroups-blender"))) if (a.id == "uninstall") QVERIFY(a.confirm);
    }
    void jsonForAgents() {
        auto m = menu();
        m->pin("gimp");
        m_frec->record("app:vlc.desktop");
        const QJsonObject o = m->toJson();
        QCOMPARE(o.value("count").toInt(), m->count());
        QCOMPARE(o.value("pinned").toArray().last().toObject().value("id").toString(), QString("gimp.desktop"));
        QVERIFY(o.value("pinned").toArray().last().toObject().value("pinned").toBool());
        QCOMPARE(o.value("recent").toArray().first().toObject().value("id").toString(), QString("vlc.desktop"));
        QVERIFY(o.value("categories").toArray().size() >= 8);
        const QJsonObject c = o.value("categories").toArray().first().toObject();
        QCOMPARE(c.value("title").toString(), QString("Internet"));
        QVERIFY(c.value("apps").toArray().first().toObject().contains("icon"));
        const QJsonObject b = m->appJson(m->find("taos-appgroups-blender"));
        QCOMPARE(b.value("group").toString(), QString("My apps"));
        QCOMPARE(b.value("kind").toString(), QString("group"));
        QCOMPARE(b.value("category").toString(), QString("Graphics"));
    }
};

QTEST_MAIN(TestAppMenu)
#include "launcher_test_appmenu.moc"
