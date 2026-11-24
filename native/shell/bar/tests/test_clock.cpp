// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>
#include "clock.h"

class TestClock : public QObject {
    Q_OBJECT
private slots:
    void time24() {
        const QDateTime dt(QDate(2026, 11, 18), QTime(7, 5, 42));
        QCOMPARE(Clock::formatTime(dt, QLocale::c(), true, false), QStringLiteral("07:05"));
        QCOMPARE(Clock::formatTime(dt, QLocale::c(), true, true), QStringLiteral("07:05:42"));
    }
    void time12() {
        const QDateTime dt(QDate(2026, 11, 18), QTime(15, 5, 0));
        QCOMPARE(Clock::formatTime(dt, QLocale::c(), false, false), QStringLiteral("3:05 PM"));
    }
    void shortDate() {
        const QDateTime dt(QDate(2026, 11, 18), QTime(12, 0));  // a Wednesday
        QCOMPARE(Clock::formatDate(dt, QLocale::c()), QStringLiteral("Wed 18 Nov"));
        QCOMPARE(Clock::formatDate(QDateTime(QDate(2026, 1, 2), QTime(0, 0)), QLocale::c()), QStringLiteral("Fri 2 Jan"));
    }
    void germanDateIsLocaleAware() {
        const QDateTime dt(QDate(2026, 11, 18), QTime(12, 0));
        const QString s = Clock::formatDate(dt, QLocale(QLocale::German, QLocale::Germany));
        QVERIFY2(s.startsWith(QStringLiteral("Mi")), qPrintable(s));
    }
    void minuteBoundary() {
        QCOMPARE(Clock::msToNextTick(QDateTime(QDate(2026, 11, 18), QTime(10, 0, 0, 0)), false), 60000);
        QCOMPARE(Clock::msToNextTick(QDateTime(QDate(2026, 11, 18), QTime(10, 0, 59, 500)), false), 500);
        QCOMPARE(Clock::msToNextTick(QDateTime(QDate(2026, 11, 18), QTime(10, 0, 30, 250)), true), 750);
    }
    void objectUpdatesOnSettings() {
        Clock c;
        QVERIFY(c.use24Hour());
        QVERIFY(!c.showSeconds());
        QCOMPARE(c.time().size(), 5);
        c.setShowSeconds(true);
        QCOMPARE(c.time().size(), 8);
    }
};
QTEST_APPLESS_MAIN(TestClock)
#include "test_clock.moc"
