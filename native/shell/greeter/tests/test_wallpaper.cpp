#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

#include "wallpaperprovider.h"

class TestWallpaper : public QObject {
    Q_OBJECT
    QTemporaryDir dir;
    void touch(const QString &rel) {
        QDir().mkpath(QFileInfo(dir.path() + "/" + rel).path());
        QFile f(dir.path() + "/" + rel);
        f.open(QIODevice::WriteOnly);
        f.write("x");
    }
private slots:
    void initTestCase() {
        for (const char *v : {"dark", "light"})
            for (const char *sz : {"3840x2160", "3840x1200", "1080x2400"}) touch(QString("neural/%1-%2.jpg").arg(v, sz));
        touch("flow/dark-3840x2160.jpg");
        QFile f(dir.path() + "/index.json");
        f.open(QIODevice::WriteOnly);
        f.write(R"({"default":"neural","wallpapers":[
 {"id":"neural","files":{"dark":{"3840x2160":"neural/dark-3840x2160.jpg","3840x1200":"neural/dark-3840x1200.jpg","1080x2400":"neural/dark-1080x2400.jpg"},
   "light":{"3840x2160":"neural/light-3840x2160.jpg","3840x1200":"neural/light-3840x1200.jpg","1080x2400":"neural/light-1080x2400.jpg"}}},
 {"id":"flow","files":{"dark":{"3840x2160":"flow/dark-3840x2160.jpg"}}}]})");
    }
    void picksSameShapeAsTheShellHelper_data() {
        QTest::addColumn<QSize>("screen");
        QTest::addColumn<QString>("variant");
        QTest::addColumn<QString>("expected");
        QTest::newRow("ultrawide") << QSize(3840, 1200) << "dark" << "neural/dark-3840x1200.jpg";
        QTest::newRow("4k") << QSize(3840, 2160) << "dark" << "neural/dark-3840x2160.jpg";
        QTest::newRow("1080p uses the 4k file") << QSize(1920, 1080) << "dark" << "neural/dark-3840x2160.jpg";
        QTest::newRow("phone") << QSize(1080, 2400) << "dark" << "neural/dark-1080x2400.jpg";
        QTest::newRow("small phone") << QSize(720, 1560) << "dark" << "neural/dark-1080x2400.jpg";
        QTest::newRow("light") << QSize(3840, 1200) << "light" << "neural/light-3840x1200.jpg";
        QTest::newRow("larger than any") << QSize(7680, 4320) << "dark" << "neural/dark-3840x2160.jpg";
    }
    void picksSameShapeAsTheShellHelper() {
        QFETCH(QSize, screen); QFETCH(QString, variant); QFETCH(QString, expected);
        QCOMPARE(WallpaperProvider::pick(dir.path(), variant, screen), dir.path() + "/" + expected);
    }
    void otherIdAndMissingThings() {
        QCOMPARE(WallpaperProvider::pick(dir.path(), "dark", QSize(1920, 1080), "flow"), dir.path() + "/flow/dark-3840x2160.jpg");
        QVERIFY(WallpaperProvider::pick(dir.path(), "light", QSize(1920, 1080), "flow").isEmpty());
        QVERIFY(WallpaperProvider::pick(dir.path(), "dark", QSize(1920, 1080), "nope").isEmpty());
        QVERIFY(WallpaperProvider::pick(dir.path() + "/missing", "dark", QSize(1920, 1080)).isEmpty());
    }
    void fileMissingOnDiskIsEmpty() {
        QFile::remove(dir.path() + "/neural/dark-1080x2400.jpg");
        QVERIFY(WallpaperProvider::pick(dir.path(), "dark", QSize(1080, 2400)).isEmpty());
    }
    void pathTraversalRefused() {
        QTemporaryDir d2;
        QFile f(d2.path() + "/index.json");
        f.open(QIODevice::WriteOnly);
        f.write(R"({"default":"x","wallpapers":[{"id":"x","files":{"dark":{"100x100":"../../etc/passwd"}}}]})");
        f.close();
        QVERIFY(WallpaperProvider::pick(d2.path(), "dark", QSize(100, 100)).isEmpty());
    }
    void corruptIndex() {
        QTemporaryDir d2;
        QFile f(d2.path() + "/index.json");
        f.open(QIODevice::WriteOnly);
        f.write("{{{");
        f.close();
        QVERIFY(WallpaperProvider::pick(d2.path(), "dark", QSize(100, 100)).isEmpty());
    }
};
QTEST_MAIN(TestWallpaper)
#include "test_wallpaper.moc"
