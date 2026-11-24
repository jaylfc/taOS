#include <QDir>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "dockcli.h"
#include "dockconfig.h"

class DockConfigTest : public QObject {
    Q_OBJECT
private slots:
    void roundTrip() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/taos/dock.json");   // the folder does not exist yet
        DockConfig c;
        c.position = QStringLiteral("left"); c.attached = true; c.autohide = true; c.size = QStringLiteral("large");
        c.magnify = true; c.hideInTiling = false; c.outputs = QStringLiteral("all");
        c.pinned = {QStringLiteral("a"), QStringLiteral("b")}; c.pinnedSet = true;
        QVERIFY(DockConfigFile::write(p, c));
        DockConfig r;
        QVERIFY(DockConfigFile::read(p, &r));
        QVERIFY(r == c);
        // atomic: no temp file is left beside it
        QCOMPARE(QDir(QFileInfo(p).absolutePath()).entryList(QDir::Files).size(), 1);
    }
    void defaultsAndSanitising() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/dock.json");
        DockConfig r;
        QVERIFY(!DockConfigFile::read(p, &r));
        QCOMPARE(r.position, QStringLiteral("bottom"));
        QVERIFY(!r.pinnedSet);
        QVERIFY(r.hideInTiling);
        QFile f(p);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write(R"({"position":"top","size":"huge","outputs":"some","attached":"yes","pinned":["x","x",""]})");
        f.close();
        QVERIFY(DockConfigFile::read(p, &r));
        QCOMPARE(r.position, QStringLiteral("bottom"));
        QCOMPARE(r.size, QStringLiteral("medium"));
        QCOMPARE(r.outputs, QStringLiteral("primary"));
        QVERIFY(!r.attached);
        QCOMPARE(r.pinned, QStringList{QStringLiteral("x")});
        QVERIFY(r.pinnedSet);
    }
    void liveReloadFollowsExternalWrites() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/dock.json");
        DockConfig c;
        QVERIFY(DockConfigFile::write(p, c));
        DockSettings s(p);
        QCOMPARE(s.position(), QStringLiteral("bottom"));
        QSignalSpy spy(&s, &DockSettings::changed);
        c.position = QStringLiteral("right");
        QVERIFY(DockConfigFile::write(p, c));          // a CLI or another process, by atomic rename
        QTRY_COMPARE(s.position(), QStringLiteral("right"));
        QVERIFY(spy.count() >= 1);
        c.autohide = true;
        QVERIFY(DockConfigFile::write(p, c));          // and again: the watch survives the rename
        QTRY_VERIFY(s.autohide());
        QSignalSpy pin(&s, &DockSettings::pinnedChanged);
        c.pinned = {QStringLiteral("z")}; c.pinnedSet = true;
        QVERIFY(DockConfigFile::write(p, c));
        QTRY_COMPARE(pin.count(), 1);
        QCOMPARE(s.config().pinned, QStringList{QStringLiteral("z")});
    }
    void aFileThatAppearsLaterIsPickedUp() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/dock.json");
        DockSettings s(p);
        QVERIFY(!s.autohide());
        DockConfig c; c.autohide = true;
        QVERIFY(DockConfigFile::write(p, c));
        QTRY_VERIFY(s.autohide());
    }
    void setWritesAndRejectsBadValues() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/dock.json");
        DockSettings s(p, false);
        QVERIFY(s.set(QStringLiteral("size"), QStringLiteral("small")));
        QVERIFY(!s.set(QStringLiteral("size"), QStringLiteral("tiny")));
        QVERIFY(!s.lastError().isEmpty());
        QVERIFY(!s.set(QStringLiteral("nonsense"), QStringLiteral("x")));
        QVERIFY(s.set(QStringLiteral("hideInTiling"), QStringLiteral("off")));
        DockConfig r;
        QVERIFY(DockConfigFile::read(p, &r));
        QCOMPARE(r.size, QStringLiteral("small"));
        QVERIFY(!r.hideInTiling);
    }
    void cliGetSetPinList() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/dock.json");
        const QStringList dirs = {QStringLiteral(TAOS_DOCK_FIXTURES "/applications")};
        QString out, err;
        QCOMPARE(DockCli::run({QStringLiteral("set"), QStringLiteral("position"), QStringLiteral("left")}, p, &out, &err, dirs), 0);
        out.clear();
        QCOMPARE(DockCli::run({QStringLiteral("get"), QStringLiteral("position")}, p, &out, &err, dirs), 0);
        QCOMPARE(out, QStringLiteral("left\n"));
        QCOMPARE(DockCli::run({QStringLiteral("set"), QStringLiteral("position"), QStringLiteral("up")}, p, &out, &err, dirs), 1);
        QVERIFY(!err.isEmpty());
        // a new person's pinned list is the defaults; pin adds to it, unpin removes
        out.clear();
        QCOMPARE(DockCli::run({QStringLiteral("list")}, p, &out, &err, dirs), 0);
        QVERIFY(out.contains(QStringLiteral("my.taos.dashboard")));
        QVERIFY(out.contains(QStringLiteral("foot")));
        QCOMPARE(DockCli::run({QStringLiteral("unpin"), QStringLiteral("foot")}, p, &out, &err, dirs), 0);
        out.clear();
        QCOMPARE(DockCli::run({QStringLiteral("list")}, p, &out, &err, dirs), 0);
        QVERIFY(!out.contains(QStringLiteral("foot\t")));
        QCOMPARE(DockCli::run({QStringLiteral("pin"), QStringLiteral("foot.desktop")}, p, &out, &err, dirs), 0);
        QCOMPARE(DockCli::run({QStringLiteral("pin"), QStringLiteral("does-not-exist")}, p, &out, &err, dirs), 1);
        DockConfig r;
        QVERIFY(DockConfigFile::read(p, &r));
        QVERIFY(r.pinned.contains(QStringLiteral("foot")));
        QCOMPARE(r.position, QStringLiteral("left"));   // pin did not disturb the other keys
    }
};
QTEST_MAIN(DockConfigTest)
#include "test_dockconfig.moc"
