#include <QtTest>

#include "desktopentry.h"

using namespace taos::search;

static const char *kFx = R"([Desktop Entry]
Type=Application
Name=Firefox
Name[de]=Feuerfuchs
Name[de_AT]=Feuerfuchs AT
GenericName=Web Browser
Comment=Browse the web
Icon=firefox
Exec=firefox %u
Keywords=internet;www;browser;
Keywords[de]=netz;
Categories=Network;WebBrowser;
Actions=new-window;private;

[Desktop Action new-window]
Name=New Window
Exec=firefox --new-window

[Desktop Action private]
Name=Private Window
Name[de]=Privates Fenster
Exec=firefox --private-window
)";

class TestDesktopEntry : public QObject {
    Q_OBJECT
private slots:
    void parsesFields() {
        const DesktopEntry e = parseDesktopEntry(kFx, "firefox.desktop", "/x/firefox.desktop", "C");
        QVERIFY(e.valid());
        QCOMPARE(e.name, QString("Firefox"));
        QCOMPARE(e.genericName, QString("Web Browser"));
        QCOMPARE(e.icon, QString("firefox"));
        QCOMPARE(e.keywords, QStringList({ "internet", "www", "browser" }));
        QCOMPARE(e.categories, QStringList({ "Network", "WebBrowser" }));
        QCOMPARE(e.actions.size(), 2);
        QCOMPARE(e.actions[0].name, QString("New Window"));
        QCOMPARE(e.actions[1].exec, QString("firefox --private-window"));
    }
    void localisedNames() {
        QCOMPARE(parseDesktopEntry(kFx, "a", "a", "de_DE.UTF-8").name, QString("Feuerfuchs"));      // lang match
        QCOMPARE(parseDesktopEntry(kFx, "a", "a", "de_AT.UTF-8").name, QString("Feuerfuchs AT"));   // most specific wins
        QCOMPARE(parseDesktopEntry(kFx, "a", "a", "fr_FR").name, QString("Firefox"));               // no match: the plain Name
        const DesktopEntry de = parseDesktopEntry(kFx, "a", "a", "de");
        QCOMPARE(de.keywords, QStringList({ "netz" }));
        QCOMPARE(de.actions[1].name, QString("Privates Fenster"));
    }
    void hiddenAndInvalid() {
        QVERIFY(!parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\nExec=x\nNoDisplay=true\n", "x", "x", "C").showable({}));
        QVERIFY(!parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\nExec=x\nHidden=true\n", "x", "x", "C").showable({}));
        QVERIFY(!parseDesktopEntry("[Desktop Entry]\nType=Link\nName=X\nURL=http://x\n", "x", "x", "C").valid());
        QVERIFY(!parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\n", "x", "x", "C").valid());   // no Exec
        QVERIFY(!parseDesktopEntry("", "x", "x", "C").valid());
        QVERIFY(!parseDesktopEntry("garbage\n[[[\n=\nName=\n", "x", "x", "C").valid());
        QVERIFY(!parseDesktopEntry("Name=Outside\nExec=x\nType=Application\n", "x", "x", "C").valid());   // no group header
    }
    void onlyShowIn() {
        const DesktopEntry g = parseDesktopEntry("[Desktop Entry]\nType=Application\nName=G\nExec=g\nOnlyShowIn=GNOME;\n", "g", "g", "C");
        QVERIFY(!g.showable({ "sway" }));
        QVERIFY(g.showable({ "GNOME" }));
        QVERIFY(g.showable({}));   // no desktop known: do not filter
        const DesktopEntry n = parseDesktopEntry("[Desktop Entry]\nType=Application\nName=N\nExec=n\nNotShowIn=sway;\n", "n", "n", "C");
        QVERIFY(!n.showable({ "sway" }));
        QVERIFY(n.showable({ "KDE" }));
    }
    void escapes() {
        const DesktopEntry e = parseDesktopEntry("[Desktop Entry]\nType=Application\nName=A\\sB\nExec=x\nKeywords=one\\;two;three;\n", "x", "x", "C");
        QCOMPARE(e.name, QString("A B"));
        QCOMPARE(e.keywords, QStringList({ "one;two", "three" }));
    }
    void execArgv() {
        DesktopEntry e;
        e.name = "App"; e.icon = "ic"; e.path = "/p/app.desktop";
        QCOMPARE(execToArgv("firefox %u", e), QStringList({ "firefox" }));
        QCOMPARE(execToArgv("foo --a %f --b %U", e), QStringList({ "foo", "--a", "--b" }));
        QCOMPARE(execToArgv("env VAR=1 \"/opt/My App/run\" --name=%c", e), QStringList({ "env", "VAR=1", "/opt/My App/run", "--name=App" }));
        QCOMPARE(execToArgv("sh -c \"echo \\\"hi\\\"\"", e), QStringList({ "sh", "-c", "echo \"hi\"" }));
        QCOMPARE(execToArgv("tool 100%% %i", e), QStringList({ "tool", "100%", "--icon", "ic" }));
        QCOMPARE(execToArgv("  spaced   out  ", e), QStringList({ "spaced", "out" }));
        QCOMPARE(execToArgv("", e), QStringList());
    }
    void desktopIds() {
        QCOMPARE(desktopIdFor("/usr/share/applications", "/usr/share/applications/org.gnome.Foo.desktop"), QString("org.gnome.Foo.desktop"));
        QCOMPARE(desktopIdFor("/usr/share/applications", "/usr/share/applications/kde4/bar.desktop"), QString("kde4-bar.desktop"));
    }
    void scanPrecedenceAndFilter() {
        QTemporaryDir a, b;
        const auto put = [](const QString &dir, const QString &name, const QString &body) {
            QFile f(dir + "/" + name);
            QVERIFY(f.open(QIODevice::WriteOnly));
            f.write(body.toUtf8());
        };
        put(a.path(), "same.desktop", "[Desktop Entry]\nType=Application\nName=FromA\nExec=a\n");
        put(b.path(), "same.desktop", "[Desktop Entry]\nType=Application\nName=FromB\nExec=b\n");
        put(b.path(), "only-b.desktop", "[Desktop Entry]\nType=Application\nName=OnlyB\nExec=b\n");
        put(a.path(), "hidden.desktop", "[Desktop Entry]\nType=Application\nName=Hid\nExec=h\nNoDisplay=true\n");
        put(b.path(), "hidden.desktop", "[Desktop Entry]\nType=Application\nName=Shown\nExec=h\n");   // a's hidden entry masks it
        put(a.path(), "badtry.desktop", "[Desktop Entry]\nType=Application\nName=Bad\nExec=x\nTryExec=/nonexistent/binary\n");
        const QList<DesktopEntry> l = scanDesktopEntries({ a.path(), b.path() }, "C", {});
        QStringList names;
        for (const auto &e : l) names << e.name;
        names.sort();
        QCOMPARE(names, QStringList({ "FromA", "OnlyB" }));
    }
};
QTEST_APPLESS_MAIN(TestDesktopEntry)
#include "launcher_test_desktopentry.moc"
