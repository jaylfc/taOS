// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QFile>
#include <QtTest>

#include "displaymodel.h"
#include "drmfixture.h"

static QList<DisplayOutput> load(const char *name) {
    QFile f(QString(TAOS_DISPLAY_FIXTURES) + "/" + name);
    if (!f.open(QIODevice::ReadOnly)) qFatal("fixture %s", name);
    QList<DisplayOutput> o;
    QString err;
    if (!parseOutputs(f.readAll(), &o, &err)) qFatal("parse %s: %s", name, qPrintable(err));
    return o;
}

class TestDisplayModel : public QObject {
    Q_OBJECT
private slots:
    void text() {
        QCOMPARE(hzText(120000), QString("120"));
        QCOMPARE(hzText(59940), QString("59.94"));
        QCOMPARE(hzText(60000), QString("60"));
        QCOMPARE(swayModeArg(3840, 1200, 120000), QString("3840x1200@120Hz"));
        QCOMPARE(swayModeArg(1920, 1080, 59940), QString("1920x1080@59.94Hz"));
        DisplayMode m; m.width = 3840; m.height = 1200; m.mHz = 120000;
        QCOMPARE(modeText(m), QString("3840 x 1200, 120 Hz"));
    }
    void parse() {
        const auto o = load("owner.json");
        QCOMPARE(o.size(), 1);
        QCOMPARE(o[0].name, QString("HDMI-A-1"));
        QCOMPARE(o[0].key(), QString("Acme UW43 S0042"));
        QCOMPARE(o[0].label(), QString("UW43"));
        QCOMPARE(o[0].current.mHz, 60000);
        QList<DisplayOutput> x;
        QVERIFY(!parseOutputs("not json", &x));
        QVERIFY(parseOutputs("[{\"name\":\"A\",\"active\":false},{\"name\":\"B\",\"make\":\"m\",\"model\":\"\",\"serial\":\"\"}]", &x));
        QCOMPARE(x.size(), 1);  // the inactive one is skipped
        QCOMPARE(x[0].key(), QString("m Unknown Unknown"));
        QCOMPARE(x[0].label(), QString("B"));  // no model: the connector name
    }
    // The owner's case: 3840x2160 modes exist but are not the panel's shape.
    void ownerPicks120AtNativeResolution() {
        auto o = load("owner.json")[0];
        o.drmPreferred = QSize(3840, 1200);
        const NativeChoice c = chooseNative(o);
        QCOMPARE(c.source, NativeChoice::Edid);
        QCOMPARE(c.mode.width, 3840);
        QCOMPARE(c.mode.height, 1200);
        QCOMPARE(c.mode.mHz, 120000);
    }
    void neverPicksLargerNonPreferred() {
        auto o = load("owner.json")[0];
        // No hints at all: keep the running resolution, best refresh there.
        NativeChoice c = chooseNative(o);
        QCOMPARE(c.source, NativeChoice::Current);
        QCOMPARE(c.mode.height, 1200);
        QCOMPARE(c.mode.mHz, 120000);
        // A bogus EDID resolution the compositor does not offer is ignored.
        o.drmPreferred = QSize(5120, 1440);
        c = chooseNative(o);
        QCOMPARE(c.source, NativeChoice::Current);
        QCOMPARE(c.mode.height, 1200);
    }
    void compositorFlagWins() {
        auto o = load("flagged.json")[0];
        o.drmPreferred = QSize(3840, 2160);  // the flag outranks the EDID hint
        const NativeChoice c = chooseNative(o);
        QCOMPARE(c.source, NativeChoice::Flag);
        QCOMPARE(c.mode.width, 2560);
        QCOMPARE(c.mode.mHz, 144000);
    }
    void laptopPanel() {
        auto o = load("laptop.json")[0];
        o.drmPreferred = QSize(2880, 1920);
        const NativeChoice c = chooseNative(o);
        QCOMPARE(c.mode.width, 2880);
        QCOMPARE(c.mode.mHz, 120000);
    }
    void twoOutputs() {
        auto outs = load("two.json");
        QCOMPARE(outs.size(), 2);
        outs[0].drmPreferred = QSize(1920, 1080);
        outs[1].drmPreferred = QSize(3840, 2160);
        QCOMPARE(chooseNative(outs[0]).mode.mHz, 60000);
        const NativeChoice c = chooseNative(outs[1]);
        QCOMPARE(c.mode.width, 3840);   // not 2560x1440@144, which is faster but not native
        QCOMPARE(c.mode.mHz, 60000);
        QCOMPARE(outs[0].label(), QString("0x05F1"));
    }
    void noPreferredInfo() {
        const auto o = load("nopref.json")[0];
        const NativeChoice c = chooseNative(o);
        QCOMPARE(c.source, NativeChoice::Current);
        QCOMPARE(c.mode.width, 1920);  // not the 3840x2160@30 that is merely larger
        QCOMPARE(c.mode.mHz, 60000);
    }
    void rejectedModesAreSkipped() {
        auto o = load("owner.json")[0];
        o.drmPreferred = QSize(3840, 1200);
        DisplayMode bad; bad.width = 3840; bad.height = 1200; bad.mHz = 120000;
        QCOMPARE(chooseNative(o, {bad}).mode.mHz, 100000);
        DisplayMode bad2 = bad; bad2.mHz = 100000;
        QCOMPARE(chooseNative(o, {bad, bad2}).mode.mHz, 60000);
    }
    void rates() {
        const auto o = load("owner.json")[0];
        QCOMPARE(ratesAt(o, QSize(3840, 1200)), (QList<int>{120000, 100000, 60000}));
        QVERIFY(ratesAt(o, QSize(1, 1)).isEmpty());
    }
    void scales() {
        QCOMPARE(recommendedScale(3840, 1200, 1043, 326), 1.0);   // the 43 inch class ultrawide
        QCOMPARE(recommendedScale(3840, 2160, 597, 336), 1.25);   // 27 inch 4K
        QCOMPARE(recommendedScale(1920, 1080, 294, 165), 1.25);   // 13.3 inch full HD
        QCOMPARE(recommendedScale(3840, 2160, 346, 194), 2.0);    // 15.6 inch 4K
        QCOMPARE(recommendedScale(1920, 1080, 531, 299), 1.0);    // 24 inch full HD
        QCOMPARE(recommendedScale(3840, 2160, 698, 392), 1.0);    // 32 inch 4K, already about 140 dpi
        QCOMPARE(recommendedScale(2880, 1920, 301, 201), 2.0);    // 14 inch laptop
        QCOMPARE(recommendedScale(3840, 2160, 0, 0), 1.0);        // unknown size
        QCOMPARE(recommendedScale(3840, 2160, 160, 90), 1.0);     // EDID aspect placeholder
        QCOMPARE(recommendedScale(3840, 2160, 1000, 100), 1.0);   // aspect disagrees with the pixels
        QCOMPARE(recommendedScale(800, 600, 10, 8), 1.0);         // absurd density
        QCOMPARE(scalePercent(1.25), 125);
        QCOMPARE(scalePercent(1.1), 110);
        QCOMPARE(scalePresets().size(), 5);
    }
    void edid() {
        QCOMPARE(edidPhysicalMm(makeEdid(1043, 326)), QSizeF(1043, 326));
        QByteArray e(128, '\0');  // no timing descriptor: the cm fields
        e[21] = 60; e[22] = 34;
        QCOMPARE(edidPhysicalMm(e), QSizeF(600, 340));
        QVERIFY(edidPhysicalMm(QByteArray(128, '\0')).isEmpty());
        QVERIFY(edidPhysicalMm("short").isEmpty());
    }
    void sysfs() {
        QTemporaryDir d;
        addConnector(d.path(), "HDMI-A-1", "3840x1200\n3840x1200\n3840x2160\n1920x1080\n", makeEdid(1043, 326));
        addConnector(d.path(), "DP-1", "1920x1080i\n", {});
        const DrmHints h = readDrmHints("HDMI-A-1", d.path());
        QCOMPARE(h.preferred, QSize(3840, 1200));
        QCOMPARE(h.physicalMm, QSizeF(1043, 326));
        const DrmHints i = readDrmHints("DP-1", d.path());
        QVERIFY(!i.preferred.isValid());  // interlaced first line: not trusted as native
        QVERIFY(!readDrmHints("eDP-1", d.path()).preferred.isValid());
        QVERIFY(!readDrmHints("HDMI-A-1", d.path() + "/nope").preferred.isValid());
    }
    void xwayland() {
        QFile a(QString(TAOS_DISPLAY_FIXTURES) + "/tree-xwayland.json"), b(QString(TAOS_DISPLAY_FIXTURES) + "/tree-plain.json");
        QVERIFY(a.open(QIODevice::ReadOnly) && b.open(QIODevice::ReadOnly));
        QVERIFY(treeHasXwayland(a.readAll()));
        QVERIFY(!treeHasXwayland(b.readAll()));
        QVERIFY(!treeHasXwayland("garbage"));
    }
};
QTEST_APPLESS_MAIN(TestDisplayModel)
#include "test_displaymodel.moc"
