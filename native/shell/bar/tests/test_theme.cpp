#include <QtTest>
#include "theme.h"

static const QString kBuiltin = QStringLiteral(TAOS_COMMON_DIR "/resources/themes/taos-default/theme.json");

static const char *const kJson0 = R"({"id":"mine","variants":["dark"],"colors":{"dark":{"ground":"#112233","stroke":"#FFFFFF10","text":"#EEEEEE","accent":"#FF0000"}},"motion":{"reduce":true}})";
static const char *const kJson1 = R"({"id":"old","variants":["dark","light"],"keyboard":{}})";
static const char *const kJson2 = R"({"id":"darkonly","variants":["dark"]})";

class TestTheme : public QObject {
    Q_OBJECT
private slots:
    void builtinDark() {
        Theme t;
        QVERIFY(t.load("taos-default", "dark", {}, kBuiltin, false));
        QCOMPARE(t.source(), QStringLiteral("builtin"));
        QCOMPARE(t.variant(), QStringLiteral("dark"));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#0E0E10"));
        QCOMPARE(t.barBackground().alpha(), 184);  // 72% of 255
        QVERIFY(!t.reduceMotion());
    }
    void builtinLight() {
        Theme t;
        QVERIFY(t.load("taos-default", "light", {}, kBuiltin, false));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#F4F4F7"));
        QCOMPARE(t.text().name().toUpper(), QStringLiteral("#17171A"));
    }
    void strokeUsesCssAlphaOrder() {
        Theme t;
        QVERIFY(t.load("taos-default", "dark", {}, kBuiltin, false));
        QCOMPARE(t.stroke().red(), 255);
        QCOMPARE(t.stroke().alpha(), 0x1F);
    }
    void installedThemeWins() {
        QTemporaryDir d;
        QDir().mkpath(d.path() + "/mine");
        QFile f(d.path() + "/mine/theme.json");
        f.open(QIODevice::WriteOnly);
        f.write(kJson0);
        f.close();
        Theme t;
        QVERIFY(t.load("mine", "dark", {d.path()}, kBuiltin, false));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#112233"));
        QVERIFY(t.source().endsWith("/mine/theme.json"));
        QVERIFY(t.reduceMotion());
        // keys the package leaves out come from the built-in copy
        QVERIFY(t.warning().isValid());
    }
    void oldPackageWithoutColorsFallsBack() {
        QTemporaryDir d;
        QDir().mkpath(d.path() + "/old");
        QFile f(d.path() + "/old/theme.json");
        f.open(QIODevice::WriteOnly);
        f.write(kJson1);
        f.close();
        Theme t;
        QVERIFY(t.load("old", "light", {d.path()}, kBuiltin, false));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#F4F4F7"));
    }
    void unlistedVariantFallsBackToDark() {
        QTemporaryDir d;
        QDir().mkpath(d.path() + "/darkonly");
        QFile f(d.path() + "/darkonly/theme.json");
        f.open(QIODevice::WriteOnly);
        f.write(kJson2);
        f.close();
        Theme t;
        QVERIFY(t.load("darkonly", "light", {d.path()}, kBuiltin, false));
        QCOMPARE(t.variant(), QStringLiteral("dark"));
    }
    void reduceMotionFlag() {
        Theme t;
        QVERIFY(t.load("taos-default", "dark", {}, kBuiltin, true));
        QVERIFY(t.reduceMotion());
    }
    void nothingUsable() {
        Theme t;
        QString err;
        QVERIFY(!t.load("x", "dark", {}, "/nonexistent.json", false, &err));
        QVERIFY(!err.isEmpty());
    }
};
QTEST_APPLESS_MAIN(TestTheme)
#include "test_theme.moc"
