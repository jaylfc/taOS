// SPDX-License-Identifier: AGPL-3.0-or-later
// Spec test L8 (fail closed): the lock-state answer with an extra key, or a timeout, is treated as
// locked; an exact {"locked":false,"session":n} is unlocked and gets the full popup.
#include <QSignalSpy>
#include <QtTest>

#include "httpstub.h"
#include "lockgate.h"

using namespace Taos::Notifyd;
using namespace Qt::StringLiterals;

class TestLockGate : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void parseTable_data()
    {
        QTest::addColumn<int>("status");
        QTest::addColumn<QByteArray>("body");
        QTest::addColumn<bool>("locked");
        QTest::newRow("exact unlocked") << 200 << QByteArray(R"({"locked":false,"session":7})") << false;
        QTest::newRow("exact locked") << 200 << QByteArray(R"({"locked":true,"session":7})") << true;
        QTest::newRow("extra key") << 200 << QByteArray(R"({"locked":false,"session":1,"extra":1})") << true;
        QTest::newRow("missing session") << 200 << QByteArray(R"({"locked":false})") << true;
        QTest::newRow("missing locked") << 200 << QByteArray(R"({"session":1,"x":2})") << true;
        QTest::newRow("locked as string") << 200 << QByteArray(R"({"locked":"false","session":1})") << true;
        QTest::newRow("locked as 0") << 200 << QByteArray(R"({"locked":0,"session":1})") << true;
        QTest::newRow("session as string") << 200 << QByteArray(R"({"locked":false,"session":"1"})") << true;
        QTest::newRow("session fractional") << 200 << QByteArray(R"({"locked":false,"session":1.5})") << true;
        QTest::newRow("not an object") << 200 << QByteArray("[false]") << true;
        QTest::newRow("garbage") << 200 << QByteArray("<html>") << true;
        QTest::newRow("empty") << 200 << QByteArray() << true;
        QTest::newRow("404") << 404 << QByteArray(R"({"locked":false,"session":1})") << true;
        QTest::newRow("500") << 500 << QByteArray(R"({"locked":false,"session":1})") << true;
        QTest::newRow("no answer") << 0 << QByteArray() << true;
    }
    void parseTable()
    {
        QFETCH(int, status);
        QFETCH(QByteArray, body);
        QFETCH(bool, locked);
        QCOMPARE(parseLockState(status, body).locked, locked);
    }
    void gateAgainstAController()
    {
        HttpStub stub;
        QVERIFY(stub.listen());
        QByteArray answer = R"({"locked":false,"session":1,"extra":1})";
        bool hang = false;
        stub.handler = [&](const StubRequest &) { StubReply r; r.hang = hang; r.body = answer; return r; };
        LockGate g(stub.url(), 300);
        QVERIFY(g.locked()); // locked until an answer proves otherwise
        g.refresh();
        QTest::qWait(300);
        QVERIFY(g.locked());                         // extra key: still locked
        QCOMPARE(stub.requests.last().headers["x-taos-console"], QByteArray("1"));
        QCOMPARE(stub.requests.last().path, u"/auth/lock-state"_s);
        QSignalSpy spy(&g, &LockGate::changed);
        answer = R"({"locked":false,"session":2})";
        g.refresh();
        QTRY_VERIFY(!g.locked());                    // control: the exact answer unlocks
        QCOMPARE(spy.size(), 1);
        hang = true;                                 // the controller stops answering
        g.refresh();
        QTRY_VERIFY_WITH_TIMEOUT(g.locked(), 3000);  // a timeout locks again
    }
    void popupFormFollowsTheLockLevel()
    {
        // unlocked: full, whatever the level
        for (auto lv : {LockLevel::Full, LockLevel::Summary, LockLevel::Hidden}) {
            const PopupForm f = popupForm(false, lv, u"T"_s, u"B"_s, 3, u"Desk PC"_s);
            QCOMPARE(f.kind, PopupForm::Full);
            QCOMPARE(f.title, u"T"_s);
        }
        // locked, full: the whole notification
        QCOMPARE(popupForm(true, LockLevel::Full, u"T"_s, u"B"_s, 1, u"Desk PC"_s).body, u"B"_s);
        // locked, summary: a count line, no title and no body anywhere
        const PopupForm s = popupForm(true, LockLevel::Summary, u"CANARY-T"_s, u"CANARY-B"_s, 3, u"Desk PC"_s);
        QCOMPARE(s.kind, PopupForm::Summary);
        QCOMPARE(s.summaryLine, u"3 new from Desk PC"_s);
        QVERIFY(s.title.isEmpty() && s.body.isEmpty());
        // locked, hidden: nothing at all
        const PopupForm h = popupForm(true, LockLevel::Hidden, u"CANARY-T"_s, u"CANARY-B"_s, 3, u"Desk PC"_s);
        QCOMPARE(h.kind, PopupForm::None);
        QVERIFY(h.title.isEmpty() && h.body.isEmpty() && h.summaryLine.isEmpty());
    }
    void unknownLevelShowsLeast()
    {
        QCOMPARE(parseLockLevel(u"full"_s), LockLevel::Full);
        QCOMPARE(parseLockLevel(u"summary"_s), LockLevel::Summary);
        QCOMPARE(parseLockLevel(u"hidden"_s), LockLevel::Hidden);
        QCOMPARE(parseLockLevel(QString()), LockLevel::Hidden);
        QCOMPARE(parseLockLevel(u"FULL"_s), LockLevel::Hidden);
    }
};

QTEST_MAIN(TestLockGate)
#include "test_lockgate.moc"
