// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>
#include "battery.h"

static void put(const QString &path, const QString &text) {
    QDir().mkpath(QFileInfo(path).path());
    QFile f(path);
    f.open(QIODevice::WriteOnly);
    f.write(text.toUtf8() + "\n");
}

class TestBattery : public QObject {
    Q_OBJECT
private slots:
    void noBatteryIsHidden() {
        QTemporaryDir d;
        QVERIFY(!Battery::read(d.path()).present);
        put(d.path() + "/sys/class/power_supply/AC/type", "Mains");
        QVERIFY(!Battery::read(d.path()).present);
        Battery b(d.path());
        QVERIFY(!b.present());
        QVERIFY(b.description().isEmpty());
    }
    void discharging() {
        QTemporaryDir d;
        const QString b = d.path() + "/sys/class/power_supply/BAT0/";
        put(b + "type", "Battery");
        put(b + "capacity", "63");
        put(b + "status", "Discharging");
        const auto r = Battery::read(d.path());
        QVERIFY(r.present);
        QCOMPARE(r.percent, 63);
        QVERIFY(!r.charging);
    }
    void charging() {
        QTemporaryDir d;
        const QString b = d.path() + "/sys/class/power_supply/BAT1/";
        put(b + "type", "Battery");
        put(b + "capacity", "100");
        put(b + "status", "Charging");
        const auto r = Battery::read(d.path());
        QVERIFY(r.present && r.charging);
        QCOMPARE(r.percent, 100);
    }
    void garbageCapacityIgnored() {
        QTemporaryDir d;
        const QString b = d.path() + "/sys/class/power_supply/BAT0/";
        put(b + "type", "Battery");
        put(b + "capacity", "n/a");
        QVERIFY(!Battery::read(d.path()).present);
    }
};
QTEST_APPLESS_MAIN(TestBattery)
#include "test_battery.moc"
