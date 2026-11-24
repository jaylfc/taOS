// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>
#include "theme.h"

static const QString kBuiltin = QStringLiteral(TAOS_COMMON_DIR "/resources/themes/graphite/theme.json");


class TestTheme : public QObject {
    Q_OBJECT
private slots:
    void builtinDark() {
        Theme t;
        QVERIFY(t.load("graphite", "dark", {}, kBuiltin, false));
        QCOMPARE(t.variant(), QStringLiteral("dark"));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#0B0B0C"));
        QCOMPARE(t.barBackground().alpha(), 184);  // 72% of 255
        QVERIFY(!t.reduceMotion());
    }
    void builtinLight() {
        Theme t;
        QVERIFY(t.load("graphite", "light", {}, kBuiltin, false));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#EBEBEC"));
        QCOMPARE(t.text().name().toUpper(), QStringLiteral("#161617"));
    }
    void strokeUsesCssAlphaOrder() {
        Theme t;
        QVERIFY(t.load("graphite", "dark", {}, kBuiltin, false));
        QCOMPARE(t.stroke().red(), 255);
        QCOMPARE(t.stroke().alpha(), 0x14);
    }
    void reduceMotionFlag() {
        Theme t;
        QVERIFY(t.load("graphite", "dark", {}, kBuiltin, true));
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
