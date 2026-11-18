#include <QtTest>
#include "volume.h"

class TestVolume : public QObject {
    Q_OBJECT
private slots:
    void plain() {
        const auto r = Volume::parse("Volume: 0.45\n");
        QVERIFY(r.ok);
        QCOMPARE(r.percent, 45);
        QVERIFY(!r.muted);
    }
    void muted() {
        const auto r = Volume::parse("Volume: 0.30 [MUTED]\n");
        QVERIFY(r.ok);
        QCOMPARE(r.percent, 30);
        QVERIFY(r.muted);
    }
    void overAmplified() {
        QCOMPARE(Volume::parse("Volume: 1.20\n").percent, 120);
    }
    void rounding() {
        QCOMPARE(Volume::parse("Volume: 0.335\n").percent, 34);
        QCOMPARE(Volume::parse("Volume: 0.00\n").percent, 0);
    }
    void garbage() {
        QVERIFY(!Volume::parse("").ok);
        QVERIFY(!Volume::parse("Could not connect to PipeWire\n").ok);
    }
};
QTEST_APPLESS_MAIN(TestVolume)
#include "test_volume.moc"
