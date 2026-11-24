// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "fuzzy.h"

using namespace taos::search;

class TestFuzzy : public QObject {
    Q_OBJECT
private slots:
    void tiers() {
        const double exact = fuzzyScore("firefox", "Firefox");
        const double prefix = fuzzyScore("fire", "Firefox");
        const double wordStart = fuzzyScore("fox", "Fire Fox");
        const double substring = fuzzyScore("refo", "Firefox");
        const double subseq = fuzzyScore("ffx", "Firefox");
        QCOMPARE(exact, 1.0);
        QVERIFY(exact > prefix);
        QVERIFY(prefix > wordStart);
        QVERIFY(wordStart > substring);
        QVERIFY(substring > subseq);
        QVERIFY(subseq > 0);
    }
    void noMatch() {
        QCOMPARE(fuzzyScore("zzz", "Firefox"), 0.0);
        QCOMPARE(fuzzyScore("", "Firefox"), 0.0);
        QCOMPARE(fuzzyScore("fire", ""), 0.0);
        QCOMPARE(fuzzyScore("xf", "Firefox"), 0.0);   // order matters for a subsequence
    }
    void caseInsensitive() { QCOMPARE(fuzzyScore("FIREFOX", "firefox"), 1.0); }
    void multiWord() {
        QVERIFY(fuzzyScore("visual studio", "Visual Studio Code") > 0.5);
        QCOMPARE(fuzzyScore("visual nothing", "Visual Studio Code"), 0.0);   // every word must match
    }
    void oneCharNeedsPrefixOrWordStart() {
        QVERIFY(fuzzyScore("f", "Firefox") > 0.8);
        QCOMPARE(fuzzyScore("x", "Firefoz"), 0.0);
        QVERIFY(fuzzyScore("c", "Visual Studio Code") > 0.5);   // start of the last word
    }
    void shorterNameWinsOnPrefix() { QVERIFY(fuzzyScore("term", "Terminal") > fuzzyScore("term", "Terminal Emulator Pro Edition")); }
    void hugeInputIsBounded() {
        const QString big(5000, QLatin1Char('a'));
        QElapsedTimer t; t.start();
        fuzzyScore("zzzzzzzz", big);
        QVERIFY(t.elapsed() < 200);
    }
};
QTEST_APPLESS_MAIN(TestFuzzy)
#include "launcher_test_fuzzy.moc"
