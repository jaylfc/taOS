#include <QFile>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "weathermodel.h"

static const QDateTime kNow = QDateTime(QDate(2026, 11, 18), QTime(12, 0), Qt::UTC);
static QByteArray cache(const QString &updated, const QString &extra = QString(), int code = 3, const QString &temp = "8.4") {
    return QString(R"({"place":"Example Town","temperature":%1,"units":"metric","code":%2,"is_day":1,"condition":"Overcast","updated":"%3"%4})")
        .arg(temp).arg(code).arg(updated, extra).toUtf8();
}

class TestWeather : public QObject {
    Q_OBJECT
private slots:
    void parsesAFreshFile() {
        const auto r = WeatherModel::parse(cache("2026-11-18T11:30:00Z"), kNow);
        QVERIFY(r.valid);
        QCOMPARE(r.temperature, QString("8") + QChar(0xB0));
        QCOMPARE(r.condition, QString("Overcast"));
        QCOMPARE(r.place, QString("Example Town"));
        QCOMPARE(r.icon, QString("cloudy"));
        QVERIFY(r.isDay);
    }
    void roundsAndSigns() {
        QCOMPARE(WeatherModel::parse(cache("2026-11-18T11:30:00Z", "", 0, "-3.6"), kNow).temperature, QString("-4") + QChar(0xB0));
        QCOMPARE(WeatherModel::parse(cache("2026-11-18T11:30:00Z", "", 0, "21.5"), kNow).temperature, QString("22") + QChar(0xB0));
    }
    void freshnessBoundary() {
        QVERIFY(WeatherModel::parse(cache("2026-11-18T09:00:01Z"), kNow).valid);   // 2h59m59s
        QVERIFY(WeatherModel::parse(cache("2026-11-18T09:00:00Z"), kNow).valid);   // exactly 3h
        QVERIFY(!WeatherModel::parse(cache("2026-11-18T08:59:59Z"), kNow).valid);  // just over
        QVERIFY(!WeatherModel::parse(cache("2026-11-17T12:00:00Z"), kNow).valid);
        QVERIFY(!WeatherModel::parse(cache("2026-11-18T13:00:00Z"), kNow).valid);  // from the future
        QVERIFY(WeatherModel::parse(cache("2026-11-18T12:05:00Z"), kNow).valid);   // small clock skew is fine
    }
    void epochUpdatedAccepted() {
        const QByteArray j = QString(R"({"temperature":10,"code":1,"is_day":0,"updated":%1})").arg(kNow.toSecsSinceEpoch() - 60).toUtf8();
        const auto r = WeatherModel::parse(j, kNow);
        QVERIFY(r.valid);
        QVERIFY(!r.isDay);
        QCOMPARE(r.condition, QString("Mostly clear"));  // text from the code when absent
        QVERIFY(r.place.isEmpty());
    }
    void malformedIsNoWeather_data() {
        QTest::addColumn<QByteArray>("json");
        QTest::newRow("empty") << QByteArray();
        QTest::newRow("garbage") << QByteArray("\x00\xff garbage");
        QTest::newRow("array") << QByteArray("[1,2]");
        QTest::newRow("string root") << QByteArray("\"hi\"");
        QTest::newRow("truncated") << cache("2026-11-18T11:30:00Z").left(40);
        QTest::newRow("no temperature") << QByteArray(R"({"code":3,"updated":"2026-11-18T11:30:00Z"})");
        QTest::newRow("temperature string") << QByteArray(R"({"temperature":"warm","code":3,"updated":"2026-11-18T11:30:00Z"})");
        QTest::newRow("temperature huge") << cache("2026-11-18T11:30:00Z", "", 3, "99999");
        QTest::newRow("code fractional") << cache("2026-11-18T11:30:00Z", "", 3).replace("\"code\":3", "\"code\":3.5");
        QTest::newRow("code unknown") << cache("2026-11-18T11:30:00Z", "", 42);
        QTest::newRow("code negative") << cache("2026-11-18T11:30:00Z", "", -1);
        QTest::newRow("no updated") << QByteArray(R"({"temperature":3,"code":3})");
        QTest::newRow("updated garbage") << cache("yesterday-ish");
        QTest::newRow("updated null") << QByteArray(R"({"temperature":3,"code":3,"updated":null})");
        QTest::newRow("oversized") << QByteArray(70 * 1024, ' ');
    }
    void malformedIsNoWeather() {
        QFETCH(QByteArray, json);
        QVERIFY(!WeatherModel::parse(json, kNow).valid);
    }
    void longTextIsClipped() {
        const QByteArray j = cache("2026-11-18T11:30:00Z", QString(R"(,"x":"%1")").arg(QString(10000, 'y')));
        QVERIFY(WeatherModel::parse(j, kNow).valid);
        const QByteArray j2 = QString(R"({"place":"%1","temperature":3,"code":3,"updated":"2026-11-18T11:30:00Z"})").arg(QString(200, 'p')).toUtf8();
        QCOMPARE(WeatherModel::parse(j2, kNow).place.size(), 48);
    }
    void wmoMappingMatchesTheSharedTable() {
        QFile f(QString(TAOS_SHELL_DIR) + "/weather/wmo-codes.tsv");
        QVERIFY(f.open(QIODevice::ReadOnly));
        int rows = 0;
        while (!f.atEnd()) {
            const QString line = QString::fromUtf8(f.readLine()).trimmed();
            if (line.isEmpty() || line.startsWith('#')) continue;
            const QStringList c = line.split('\t');
            QCOMPARE(c.size(), 3);
            const int code = c[0].toInt();
            QCOMPARE(WeatherModel::iconForCode(code), c[1]);
            QCOMPARE(WeatherModel::textForCode(code), c[2]);
            ++rows;
        }
        QCOMPARE(rows, 28);
        // Codes outside the table have no icon.
        for (int code : {4, 10, 42, 58, 70, 90, 98, 100, -1}) QVERIFY2(WeatherModel::iconForCode(code).isEmpty(), qPrintable(QString::number(code)));
        // Every code 0..99 is either mapped or has neither icon nor text.
        for (int code = 0; code < 100; ++code)
            QCOMPARE(WeatherModel::iconForCode(code).isEmpty(), WeatherModel::textForCode(code).isEmpty());
    }
    void dayNightIcons() {
        QTemporaryDir d;
        const QString p = d.path() + "/w.json";
        const QByteArray night = QString(R"({"temperature":3,"code":0,"is_day":0,"updated":"%1"})")
                                     .arg(QDateTime::currentDateTimeUtc().toString(Qt::ISODate)).toUtf8();
        QFile f(p); QVERIFY(f.open(QIODevice::WriteOnly)); f.write(night); f.close();
        WeatherModel m;
        m.setPath(p);
        QSignalSpy changed(&m, &WeatherModel::changed);
        m.refresh();
        QVERIFY(m.available());
        QCOMPARE(m.icon(), QString("clear-night"));
        QCOMPARE(changed.size(), 1);
        m.refresh();
        QCOMPARE(changed.size(), 1);  // unchanged file: no signal
        QFile::remove(p);
        m.refresh();
        QVERIFY(!m.available());
        QCOMPARE(m.icon(), QString());
        QCOMPARE(changed.size(), 2);
    }
    void missingFileIsQuiet() {
        WeatherModel m;
        m.setPath("/nonexistent/dir/weather.json");
        m.refresh();
        QVERIFY(!m.available());
        QVERIFY(m.temperature().isEmpty());
    }
};
QTEST_MAIN(TestWeather)
#include "test_weather.moc"
