#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTemporaryDir>
#include <QtTest>

#include "displayconfig.h"

static DisplayEntry E(const QString &k, int w, int h, int mhz, double s) {
    DisplayEntry e; e.key = k; e.width = w; e.height = h; e.mHz = mhz; e.scale = s; return e;
}
static QString slurp(const QString &p) { QFile f(p); return f.open(QIODevice::ReadOnly) ? QString::fromUtf8(f.readAll()) : QString(); }

class TestDisplayConfig : public QObject {
    Q_OBJECT
private slots:
    void lineFormat() {
        QMap<QString, DisplayEntry> m;
        m["Acme UW43 S0042"] = E("Acme UW43 S0042", 3840, 1200, 120000, 1.0);
        m["Contoso P27 Unknown"] = E("Contoso P27 Unknown", 1920, 1080, 59940, 1.25);
        const QString t = DisplayConfigFile::render(m);
        QVERIFY(t.contains("output \"Acme UW43 S0042\" mode 3840x1200@120Hz scale 1\n"));
        QVERIFY(t.contains("output \"Contoso P27 Unknown\" mode 1920x1080@59.94Hz scale 1.25\n"));
        QVERIFY(!t.contains(QChar(0x2014)));
    }
    void scaleOnlyAndModeOnly() {
        DisplayEntry s; s.key = "A B C"; s.scale = 1.5;
        DisplayEntry m; m.key = "D E F"; m.width = 1920; m.height = 1080; m.mHz = 60000;
        const QString t = DisplayConfigFile::render({{s.key, s}, {m.key, m}});
        QVERIFY(t.contains("output \"A B C\" scale 1.5\n"));
        QVERIFY(t.contains("output \"D E F\" mode 1920x1080@60Hz\n"));
    }
    void oddNamesRoundTrip() {
        const QStringList keys = {
            "Weird \"Mon\" back\\slash Unknown",
            "Dollar $HOME; semi, comma {brace} S1",
            QString::fromUtf8("Ünïcode Müller 显示器 1"),
            "  leading and  double  spaces  X",
            "tab\there",
        };
        QMap<QString, DisplayEntry> m;
        for (const QString &k : keys) m[k] = E(k, 2560, 1440, 144000, 1.75);
        const QString t = DisplayConfigFile::render(m);
        const auto back = DisplayConfigFile::parse(t);
        QCOMPARE(back.size(), keys.size());
        for (const QString &k : keys) {
            QString want = k;
            want.replace('\t', ' ');
            QVERIFY2(back.contains(want), qPrintable(k));
            QCOMPARE(back[want].mHz, 144000);
            QCOMPARE(back[want].scale, 1.75);
        }
        // One line per monitor even with a newline in the name.
        QMap<QString, DisplayEntry> nl;
        nl["bad\nname Unknown Unknown"] = E("bad\nname Unknown Unknown", 1, 1, 1000, 1.0);
        QCOMPARE(DisplayConfigFile::render(nl).count('\n'), 3);  // two comment lines and one output line
    }
    void quoting() {
        QCOMPARE(DisplayConfigFile::quote("a b"), QString("\"a b\""));
        QCOMPARE(DisplayConfigFile::quote("a\"b"), QString("\"a\\\"b\""));
        QCOMPARE(DisplayConfigFile::quote("a\\b"), QString("\"a\\\\b\""));
        QCOMPARE(DisplayConfigFile::quote("a\nb"), QString("\"a b\""));
    }
    void writeAtomicAndIdempotent() {
        QTemporaryDir d;
        const QString p = d.path() + "/taos/sway.d/20-display.conf";  // directories are created
        DisplayConfigFile f(p);
        QVERIFY(f.read().isEmpty());
        QString err;
        QVERIFY2(f.upsert(E("Acme UW43 S0042", 3840, 1200, 120000, 1.0), &err), qPrintable(err));
        QVERIFY(QFile::exists(p));
        QCOMPARE(QDir(QFileInfo(p).absolutePath()).entryList(QDir::Files).size(), 1);  // no temp file left
        // Same content again: the file is not rewritten.
        const QDateTime old = QDateTime::fromSecsSinceEpoch(1000000000);
        { QFile q(p); QVERIFY(q.open(QIODevice::ReadWrite)); QVERIFY(q.setFileTime(old, QFileDevice::FileModificationTime)); }
        QVERIFY(f.upsert(E("Acme UW43 S0042", 3840, 1200, 120000, 1.0), &err));
        QCOMPARE(QFileInfo(p).lastModified().toSecsSinceEpoch(), old.toSecsSinceEpoch());
        // A change does rewrite.
        QVERIFY(f.upsert(E("Acme UW43 S0042", 3840, 1200, 120000, 1.25), &err));
        QVERIFY(QFileInfo(p).lastModified().toSecsSinceEpoch() > old.toSecsSinceEpoch());
        QCOMPARE(f.read()["Acme UW43 S0042"].scale, 1.25);
    }
    void upsertMergesAndKeepsAbsentMonitors() {
        QTemporaryDir d;
        DisplayConfigFile f(d.path() + "/x.conf");
        QVERIFY(f.upsert(E("Away Monitor 1", 1920, 1080, 60000, 1.0)));
        QVERIFY(f.upsert(E("Here Monitor 2", 3840, 2160, 60000, 1.5)));
        DisplayEntry scaleOnly; scaleOnly.key = "Here Monitor 2"; scaleOnly.scale = 2.0;
        QVERIFY(f.upsert(scaleOnly));
        const auto all = f.read();
        QCOMPARE(all.size(), 2);
        QCOMPARE(all["Here Monitor 2"].scale, 2.0);
        QCOMPARE(all["Here Monitor 2"].mHz, 60000);   // the mode survived a scale-only patch
        QCOMPARE(all["Away Monitor 1"].width, 1920);  // not connected, still kept
    }
    void unwritableTargetLeavesOriginal() {
        QTemporaryDir d;
        const QString dir = d.path() + "/ro";
        QDir().mkpath(dir);
        DisplayConfigFile f(dir + "/20-display.conf");
        QVERIFY(f.upsert(E("A B C", 1920, 1080, 60000, 1.0)));
        const QString before = slurp(f.path());
        QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::ExeOwner);
        QString err;
        const bool ok = f.upsert(E("A B C", 1920, 1080, 60000, 2.0), &err);
        QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        if (!ok) { QVERIFY(!err.isEmpty()); QCOMPARE(slurp(f.path()), before); }
        else QSKIP("running with privileges that ignore directory permissions");
    }
    void defaultPath() {
        qputenv("TAOS_DISPLAY_CONF", "/tmp/x/20.conf");
        QCOMPARE(DisplayConfigFile::defaultPath(), QString("/tmp/x/20.conf"));
        qunsetenv("TAOS_DISPLAY_CONF");
        qputenv("XDG_CONFIG_HOME", "/tmp/cfg");
        QCOMPARE(DisplayConfigFile::defaultPath(), QString("/tmp/cfg/taos/sway.d/20-display.conf"));
        qunsetenv("XDG_CONFIG_HOME");
        QVERIFY(DisplayConfigFile::defaultPath().endsWith("/.config/taos/sway.d/20-display.conf"));
    }
};
QTEST_APPLESS_MAIN(TestDisplayConfig)
#include "test_displayconfig.moc"
