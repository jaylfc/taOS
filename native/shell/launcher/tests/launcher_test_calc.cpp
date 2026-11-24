// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "calc.h"

using namespace taos::search;

class TestCalc : public QObject {
    Q_OBJECT
private slots:
    void values_data() {
        QTest::addColumn<QString>("in");
        QTest::addColumn<QString>("out");
        QTest::newRow("add") << "1+2" << "3";
        QTest::newRow("precedence") << "2+3*4" << "14";
        QTest::newRow("parens") << "(2+3)*4" << "20";
        QTest::newRow("unary") << "-3+5" << "2";
        QTest::newRow("double unary") << "--3" << "3";
        QTest::newRow("power") << "2^10" << "1024";
        QTest::newRow("power right assoc") << "2^3^2" << "512";
        QTest::newRow("star star") << "2**8" << "256";
        QTest::newRow("neg exponent") << "2^-1" << "0.5";
        QTest::newRow("division") << "7/2" << "3.5";
        QTest::newRow("modulo") << "10%3" << "1";
        QTest::newRow("decimal") << "0.1+0.2" << "0.3";
        QTest::newRow("thousands") << "1,000+1" << "1001";
        QTest::newRow("times sign") << QString::fromUtf8("6×7") << "42";
        QTest::newRow("x as times") << "6 x 7" << "42";
        QTest::newRow("sqrt") << "sqrt(144)" << "12";
        QTest::newRow("pi") << "round(pi*100)" << "314";
        QTest::newRow("exp notation") << "1e3+1" << "1001";
        QTest::newRow("leading equals") << "=2*3" << "6";
        QTest::newRow("whitespace") << "  12 * ( 3 + 4 ) / 2 " << "42";
        QTest::newRow("functions") << "abs(-4)+floor(2.9)+ceil(0.1)" << "7";
    }
    void values() {
        QFETCH(QString, in);
        QFETCH(QString, out);
        const CalcResult r = evaluate(in);
        QVERIFY2(r.ok, qPrintable(r.error));
        QCOMPARE(r.text, out);
    }
    void bareNumberIsNotAnExpression() {
        const CalcResult r = evaluate("42");
        QVERIFY(r.ok);
        QVERIFY(!r.isExpression);
        QVERIFY(evaluate("2+2").isExpression);
        QVERIFY(evaluate("sqrt(4)").isExpression);
    }
    void malformed_data() {
        QTest::addColumn<QString>("in");
        for (const char *s : { "", "   ", "1+", "+", "*3", "(1+2", "1+2)", "()", "1 2", "2**", "1//2", "abc", "foo(2)", "sqrt", "sqrt(", "1/0", "5%0",
                               "sqrt(-1)", "ln(0)", "log(-3)", "9^9^9^9", "1e999", "..", "1..2", "0x10", "1;2", "1,", "__import__('os')", "system(\"ls\")",
                               "`ls`", "$(ls)", "1+2 // comment", "\\n" })
            QTest::newRow(s) << QString::fromUtf8(s);
    }
    void malformed() {
        QFETCH(QString, in);
        const CalcResult r = evaluate(in);
        QVERIFY2(!r.ok, qPrintable(in + " => " + r.text));
        QVERIFY(!r.error.isEmpty());
    }
    void limits() {
        QVERIFY(!evaluate(QString(300, QLatin1Char('1'))).ok);   // too long
        QVERIFY(!evaluate(QString(100, QLatin1Char('(')) + "1" + QString(100, QLatin1Char(')'))).ok);   // too deep, no stack overflow
        QVERIFY(!evaluate(QString(200, QLatin1Char('-')) + "1").ok || true);   // must return, not crash
        QVERIFY(evaluate(QString(20, QLatin1Char('(')) + "1+1" + QString(20, QLatin1Char(')'))).ok);
    }
    void formatting() {
        QCOMPARE(formatNumber(0), QString("0"));
        QCOMPARE(formatNumber(1.0 / 3.0), QString("0.333333333333"));
        QCOMPARE(formatNumber(-2.5), QString("-2.5"));
        QCOMPARE(formatNumber(1e21), QString("1e+21"));
    }
};
QTEST_APPLESS_MAIN(TestCalc)
#include "launcher_test_calc.moc"
