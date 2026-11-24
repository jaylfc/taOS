// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QFile>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "poweractions.h"

class TestPower : public QObject {
    Q_OBJECT
    QTemporaryDir dir;
    QString stub() {
        const QString p = dir.path() + "/stub.sh";
        QFile f(p);
        f.open(QIODevice::WriteOnly);
        f.write(QByteArray("#!/bin/sh\necho \"$@\" >> \"") + (dir.path() + "/ran.log").toUtf8() + "\"\n");
        f.close();
        QFile::setPermissions(p, QFile::permissions(p) | QFile::ExeOwner);
        return p;
    }
    QStringList ran() {
        QFile f(dir.path() + "/ran.log");
        if (!f.open(QIODevice::ReadOnly)) return {};
        return QString::fromUtf8(f.readAll()).split('\n', Qt::SkipEmptyParts);
    }
private slots:
    void verbs() {
        QCOMPARE(PowerActions::verbFor("sleep"), QString("suspend"));
        QCOMPARE(PowerActions::verbFor("restart"), QString("reboot"));
        QCOMPARE(PowerActions::verbFor("shutdown"), QString("poweroff"));
        QVERIFY(PowerActions::verbFor("format-disk").isEmpty());
    }
    void firstClickArmsSecondRuns() {
        PowerActions p;
        p.setCommand(stub());
        QSignalSpy armed(&p, &PowerActions::armedChanged), ranSpy(&p, &PowerActions::ran);
        p.activate("restart");
        QCOMPARE(p.armed(), QString("restart"));
        QVERIFY(ranSpy.isEmpty());
        QVERIFY(ran().isEmpty());
        p.activate("restart");
        QVERIFY(p.armed().isEmpty());
        QCOMPARE(ranSpy.size(), 1);
        QCOMPARE(ranSpy.at(0).at(0).toString(), QString("reboot"));
        QTRY_COMPARE_WITH_TIMEOUT(ran(), QStringList({"reboot"}), 3000);
    }
    void armedTimesOut() {
        PowerActions p;
        p.setCommand(stub());
        p.setConfirmMs(120);
        p.activate("shutdown");
        QCOMPARE(p.armed(), QString("shutdown"));
        QTRY_VERIFY_WITH_TIMEOUT(p.armed().isEmpty(), 2000);
        p.activate("shutdown");  // arms again, does not run
        QCOMPARE(p.armed(), QString("shutdown"));
    }
    void armingAnotherActionSwitches() {
        PowerActions p;
        p.setCommand(stub());
        QSignalSpy ranSpy(&p, &PowerActions::ran);
        p.activate("sleep");
        p.activate("restart");
        QCOMPARE(p.armed(), QString("restart"));
        QVERIFY(ranSpy.isEmpty());
    }
    void unknownActionIgnored() {
        PowerActions p;
        p.setCommand(stub());
        p.activate("rm -rf");
        QVERIFY(p.armed().isEmpty());
    }
    void envStubIsHonoured() {
        QFile::remove(dir.path() + "/ran.log");
        qputenv("TAOS_GREETER_POWER_CMD", stub().toUtf8());
        PowerActions p;
        p.activate("sleep");
        p.activate("sleep");
        QTRY_COMPARE_WITH_TIMEOUT(ran(), QStringList({"suspend"}), 3000);
        qunsetenv("TAOS_GREETER_POWER_CMD");
    }
};
QTEST_MAIN(TestPower)
#include "test_poweractions.moc"
