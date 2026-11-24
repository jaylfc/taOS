#include <QtTest>

#include "appearance.h"
#include "testenv.h"

class TestAppearance : public QObject {
    Q_OBJECT
private slots:
    void defaultsWhenMissing() {
        TestHome h;
        Appearance a;
        a.theme = "x";
        QVERIFY(!AppearanceFile::read(h.appearance(), &a));
        QCOMPARE(a.theme, QStringLiteral("graphite"));
        QCOMPARE(a.mode, QStringLiteral("dark"));
    }
    void pathsFollowTheEnvironment() {
        TestHome h;
        QCOMPARE(AppearanceFile::userPath(), h.appearance());
        qunsetenv("TAOS_APPEARANCE");
        qputenv("XDG_CONFIG_HOME", "/tmp/xdgcfg");
        QCOMPARE(AppearanceFile::userPath(), QStringLiteral("/tmp/xdgcfg/taos/appearance.json"));
        qunsetenv("XDG_CONFIG_HOME");
        QVERIFY(AppearanceFile::userPath().endsWith("/.config/taos/appearance.json"));
        qunsetenv("TAOS_SYSTEM_APPEARANCE");
        QCOMPARE(AppearanceFile::systemPath(), QStringLiteral("/etc/taos/appearance.json"));
        qunsetenv("TAOS_APPEARANCE_MIRROR_DIR");
        QCOMPARE(AppearanceFile::mirrorDir(), QStringLiteral("/var/lib/taos/appearance"));
    }
    void roundTrip() {
        TestHome h;
        for (const QString &m : {"dark", "light", "auto"}) {
            Appearance in;
            in.theme = "graphite-ice";
            in.mode = m;
            QString err;
            QVERIFY2(AppearanceFile::write(h.appearance(), in, &err), qPrintable(err));
            Appearance out;
            QVERIFY(AppearanceFile::read(h.appearance(), &out));
            QCOMPARE(out, in);
        }
        // The file is the documented shape.
        QFile f(h.appearance());
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
        QCOMPARE(o.value("theme").toString(), QStringLiteral("graphite-ice"));
        QCOMPARE(o.value("mode").toString(), QStringLiteral("auto"));
    }
    void windowButtonsRoundTrip() {
        TestHome h;
        Appearance a;
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        Appearance b;
        QVERIFY(AppearanceFile::read(h.appearance(), &b));
        QVERIFY(b.windowButtons.isEmpty());   // not written when it follows the theme
        QFile f(h.appearance());
        QVERIFY(f.open(QIODevice::ReadOnly));
        QVERIFY(!f.readAll().contains("windowButtons"));
        f.close();
        a.windowButtons = "right";
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        QVERIFY(AppearanceFile::read(h.appearance(), &b));
        QCOMPARE(b.windowButtons, QStringLiteral("right"));
        QVERIFY(a == b);
        a.windowButtons = "bogus";   // never written
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        QVERIFY(AppearanceFile::read(h.appearance(), &b));
        QVERIFY(b.windowButtons.isEmpty());
    }
    void writeIsAtomic() {
        TestHome h;
        Appearance a;
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        // No temp files are left beside it, and a reader running during a burst of writes
        // never sees a torn file.
        QAtomicInt stop = 0, bad = 0;
        QThread *reader = QThread::create([&] {
            while (!stop.loadRelaxed()) {
                QFile f(h.appearance());
                if (!f.open(QIODevice::ReadOnly)) { bad.ref(); continue; }  // a missing file would be a torn replace
                QJsonParseError e;
                const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &e);
                if (e.error != QJsonParseError::NoError || d.object().value("theme").toString().isEmpty()) bad.ref();
            }
        });
        reader->start();
        for (int i = 0; i < 300; ++i) {
            Appearance n;
            n.theme = (i % 2) ? "graphite" : "graphite-warm";
            n.mode = (i % 3 == 0) ? "dark" : "light";
            QVERIFY(AppearanceFile::write(h.appearance(), n));
        }
        stop.storeRelaxed(1);
        reader->wait();
        delete reader;
        QCOMPARE(bad.loadRelaxed(), 0);
        QCOMPARE(QDir(QFileInfo(h.appearance()).absolutePath()).entryList(QDir::Files).size(), 1);
    }
    void writeCreatesTheFolderAndReportsFailure() {
        TestHome h;
        Appearance a;
        QVERIFY(AppearanceFile::write(h.dir.path() + "/deep/er/appearance.json", a));
        QString err;
        // A file where a folder is needed.
        QFile blocker(h.dir.path() + "/blocker");
        blocker.open(QIODevice::WriteOnly);
        blocker.close();
        QVERIFY(!AppearanceFile::write(h.dir.path() + "/blocker/appearance.json", a, &err));
        QVERIFY(!err.isEmpty());
    }
    void worldReadableCopy() {
        TestHome h;
        Appearance a;
        const QString p = h.dir.path() + "/m/1000.json";
        QVERIFY(AppearanceFile::write(p, a, nullptr, true));
        QVERIFY(QFile::permissions(p) & QFileDevice::ReadOther);
        QVERIFY(!(QFile::permissions(p) & QFileDevice::WriteOther));
    }
    void badValuesBecomeDefaults() {
        TestHome h;
        QDir().mkpath(QFileInfo(h.appearance()).absolutePath());
        QFile f(h.appearance());
        f.open(QIODevice::WriteOnly);
        f.write(R"({"theme":"../../etc/passwd","mode":"purple"})");
        f.close();
        Appearance a;
        QVERIFY(AppearanceFile::read(h.appearance(), &a));
        QCOMPARE(a.theme, QStringLiteral("graphite"));
        QCOMPARE(a.mode, QStringLiteral("dark"));
        f.open(QIODevice::WriteOnly | QIODevice::Truncate);
        f.write("[1,2]");
        f.close();
        QVERIFY(!AppearanceFile::read(h.appearance(), &a));
    }
    void autoFollowsTheScheduleWithAFakeClock() {
        auto at = [](int hh, int mm) { return QDateTime(QDate(2026, 11, 22), QTime(hh, mm)); };
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(6, 59)), QStringLiteral("dark"));
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(7, 0)), QStringLiteral("light"));
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(12, 0)), QStringLiteral("light"));
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(18, 59)), QStringLiteral("light"));
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(19, 0)), QStringLiteral("dark"));
        QCOMPARE(AppearanceFile::resolveVariant("auto", at(0, 0)), QStringLiteral("dark"));
        QCOMPARE(AppearanceFile::resolveVariant("dark", at(12, 0)), QStringLiteral("dark"));
        QCOMPARE(AppearanceFile::resolveVariant("light", at(2, 0)), QStringLiteral("light"));
    }
};

QTEST_MAIN(TestAppearance)
#include "test_appearance.moc"
