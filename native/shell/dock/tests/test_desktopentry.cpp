// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QDir>
#include <QTemporaryDir>
#include <QtTest>

#include "desktopentry.h"
#include "dockcontroller.h"

static void write(const QString &path, const QByteArray &d) {
    QDir().mkpath(QFileInfo(path).absolutePath());
    QFile f(path);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(d);
}

class DesktopEntryTest : public QObject {
    Q_OBJECT
private slots:
    void parsesTheFields() {
        DesktopEntry e;
        QVERIFY(Desktop::parse("[Desktop Entry]\nType=Application\nName=Activity\nName[de]=Aktivitaet\nExec=taos-dashboard %U\nIcon=my.taos.dashboard\n"
                               "StartupWMClass=my.taos.dashboard\nSingleMainWindow=true\nActions=new-window;\n\n"
                               "[Desktop Action new-window]\nName=New Window\nExec=taos-dashboard --new\n", QStringLiteral("my.taos.dashboard"), &e));
        QCOMPARE(e.name, QStringLiteral("Activity"));
        QCOMPARE(e.icon, QStringLiteral("my.taos.dashboard"));
        QCOMPARE(e.startupWMClass, QStringLiteral("my.taos.dashboard"));
        QVERIFY(e.singleMainWindow);
        QVERIFY(e.newWindowAction());
        QCOMPARE(e.newWindowAction()->exec, QStringLiteral("taos-dashboard --new"));
    }
    void rejectsWhatIsNotAnApplication() {
        DesktopEntry e;
        QVERIFY(!Desktop::parse("[Desktop Entry]\nType=Link\nName=x\nExec=y\n", QStringLiteral("a"), &e));
        QVERIFY(!Desktop::parse("[Desktop Entry]\nType=Application\nName=x\nExec=y\nHidden=true\n", QStringLiteral("a"), &e));
        QVERIFY(!Desktop::parse("[Desktop Entry]\nType=Application\nName=x\n", QStringLiteral("a"), &e));   // no Exec
    }
    void matchesAChromiumWebAppWindow() {
        // Measured on the real machine: Chromium ignores --class and names the window chrome-<host>__<path>-<profile>.
        DesktopIndex idx;
        for (const char *id : {"k3j9x0a1bc", "q8w2e4r6ty"}) {
            DesktopEntry e;
            e.id = QStringLiteral("taos-webapp-") + QString::fromLatin1(id);
            e.name = QStringLiteral("YouTube ") + QString::fromLatin1(id);
            e.exec = QStringLiteral("taos-webapp launch ") + QString::fromLatin1(id);
            e.startupWMClass = QStringLiteral("chrome-www.youtube.com__-") + QString::fromLatin1(id);
            idx.add(e);
        }
        // a legacy instance still in the Default profile
        DesktopEntry old;
        old.id = QStringLiteral("taos-webapp-zzzzzzzzzz"); old.name = QStringLiteral("Old"); old.exec = QStringLiteral("taos-webapp launch zzzzzzzzzz");
        old.startupWMClass = QStringLiteral("chrome-www.youtube.com__-Default");
        idx.add(old);
        QCOMPARE(idx.matchWindow(QStringLiteral("chrome-www.youtube.com__-k3j9x0a1bc"))->id, QStringLiteral("taos-webapp-k3j9x0a1bc"));
        QCOMPARE(idx.matchWindow(QStringLiteral("chrome-www.youtube.com__-q8w2e4r6ty"))->id, QStringLiteral("taos-webapp-q8w2e4r6ty"));
        QCOMPARE(idx.matchWindow(QStringLiteral("chrome-www.youtube.com__-Default"))->id, QStringLiteral("taos-webapp-zzzzzzzzzz"));
        QVERIFY(!idx.matchWindow(QStringLiteral("chrome-www.youtube.com__-other")));   // no entry: not mismatched to a neighbour
    }
    void execLine() {
        QString p; QStringList a;
        QVERIFY(Desktop::commandLine(QStringLiteral("foo --x \"a b\" %U %f 100%%"), &p, &a));
        QCOMPARE(p, QStringLiteral("foo"));
        QCOMPARE(a, (QStringList{QStringLiteral("--x"), QStringLiteral("a b"), QStringLiteral("100%")}));
        QVERIFY(!Desktop::commandLine(QStringLiteral("%U"), &p, &a));
    }

    void appGroupAppsFoldIntoTheirOwnLauncherNotAHostCopy() {
        // each catalogue app: the window app_id its manifest declares, with a host launcher of the same program present
        const struct { const char *app, *window; } apps[] = {{"chromium", "chromium"}, {"firefox", "firefox"}, {"claude-desktop", "com.anthropic.Claude"},
                                                            {"kilo-desktop", "kilo"}, {"antigravity", "antigravity-ide"}};
        DesktopIndex idx;
        for (const char *host : {"chromium", "firefox", "antigravity-ide"}) {
            DesktopEntry h; h.id = QString::fromLatin1(host); h.name = h.id; h.exec = QString::fromLatin1(host); idx.add(h);
        }
        for (const auto &a : apps) {
            DesktopEntry e;
            e.id = QStringLiteral("taos-appgroups-") + QString::fromLatin1(a.app);
            e.name = e.id; e.exec = QStringLiteral("taos-appgroups run ") + QString::fromLatin1(a.app);
            e.startupWMClass = QString::fromLatin1(a.window); e.taosAppId = e.startupWMClass; e.containerApp = QString::fromLatin1(a.app);
            idx.add(e);
        }
        for (const auto &a : apps)
            QCOMPARE(idx.matchWindow(QString::fromLatin1(a.window))->id, QStringLiteral("taos-appgroups-") + QString::fromLatin1(a.app));
        // and the host copy still matches when nothing from a group claims it
        DesktopIndex only;
        DesktopEntry h; h.id = QStringLiteral("chromium"); h.name = h.id; h.exec = QStringLiteral("chromium"); only.add(h);
        QCOMPARE(only.matchWindow(QStringLiteral("chromium"))->id, QStringLiteral("chromium"));
    }
    void matchesWindowsToApps() {
        DesktopIndex idx;
        auto add = [&](const char *id, const char *wm, const char *exec) {
            DesktopEntry e; e.id = QString::fromLatin1(id); e.name = e.id; e.exec = QString::fromLatin1(exec); e.startupWMClass = QString::fromLatin1(wm);
            idx.add(e);
        };
        add("my.taos.dashboard", "", "taos-dashboard %U");
        add("org.gnome.Nautilus", "", "nautilus");
        add("fancy-term", "FancyTerm", "env LANG=C /usr/bin/fancyterm");
        add("foot", "", "foot");
        QCOMPARE(idx.matchWindow(QStringLiteral("my.taos.dashboard"))->id, QStringLiteral("my.taos.dashboard"));   // desktop file id
        QCOMPARE(idx.matchWindow(QStringLiteral("MY.TAOS.DASHBOARD"))->id, QStringLiteral("my.taos.dashboard"));   // case
        QCOMPARE(idx.matchWindow(QStringLiteral("fancyterm"))->id, QStringLiteral("fancy-term"));                  // StartupWMClass, then exec
        QCOMPARE(idx.matchWindow(QStringLiteral("FancyTerm"))->id, QStringLiteral("fancy-term"));                  // StartupWMClass
        QCOMPARE(idx.matchWindow(QStringLiteral("nautilus"))->id, QStringLiteral("org.gnome.Nautilus"));           // reverse DNS tail
        QCOMPARE(idx.matchWindow(QStringLiteral("foot"))->id, QStringLiteral("foot"));
        QVERIFY(!idx.matchWindow(QStringLiteral("nothing")));
        QVERIFY(!idx.matchWindow(QString()));
    }
    void scanPrefersEarlierDirectories() {
        QTemporaryDir a, b;
        write(a.path() + QStringLiteral("/applications/x.desktop"), "[Desktop Entry]\nType=Application\nName=Mine\nExec=x\n");
        write(b.path() + QStringLiteral("/applications/x.desktop"), "[Desktop Entry]\nType=Application\nName=System\nExec=x\n");
        write(b.path() + QStringLiteral("/applications/sub/y.desktop"), "[Desktop Entry]\nType=Application\nName=Y\nExec=y\n");
        DesktopIndex idx;
        idx.scan({a.path() + QStringLiteral("/applications"), b.path() + QStringLiteral("/applications")});
        QCOMPARE(idx.byId(QStringLiteral("x"))->name, QStringLiteral("Mine"));
        QVERIFY(idx.byId(QStringLiteral("sub-y")));   // subdirectories join with "-"
    }
    void defaultPinsIncludeAgentsMessagesAndSettingsInOrder() {
        QTemporaryDir d;
        for (const char *id : {"my.taos.settings", "my.taos.messages", "my.taos.agents", "my.taos.decisions"})
            write(d.path() + QStringLiteral("/applications/") + QString::fromLatin1(id) + QStringLiteral(".desktop"),
                  QByteArray("[Desktop Entry]\nType=Application\nName=App\nExec=x\n"));
        DesktopIndex idx;
        idx.scan({d.path() + QStringLiteral("/applications")});
        QCOMPARE(DockController::defaultPinned(idx), (QStringList{QStringLiteral("my.taos.decisions"), QStringLiteral("my.taos.agents"),
                                                                   QStringLiteral("my.taos.messages"), QStringLiteral("my.taos.settings")}));
    }
    void shippedAppEntriesParse() {
        DesktopIndex idx;
        idx.scan({QStringLiteral(TAOS_DOCK_FIXTURES "/applications")});
        for (const char *id : {"my.taos.dashboard", "my.taos.notifications", "my.taos.decisions"}) {
            const DesktopEntry *e = idx.byId(QString::fromLatin1(id));
            QVERIFY2(e, id);
            QCOMPARE(e->startupWMClass, QString::fromLatin1(id));
            QCOMPARE(e->icon, QString::fromLatin1(id));
        }
    }
};
QTEST_MAIN(DesktopEntryTest)
#include "test_desktopentry.moc"
