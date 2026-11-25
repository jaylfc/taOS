// SPDX-License-Identifier: AGPL-3.0-or-later
// `taos-search menu ...`: the app menu for scripts, SSH and agents, with no daemon and no window. The real binary.
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QtTest>

class TestCli : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    struct R { int rc; QString out, err; };
    R run(const QStringList &args) {
        QProcess p;
        QProcessEnvironment e;
        e.insert("PATH", "/usr/bin:/bin");
        e.insert("HOME", m_tmp.path() + "/home");
        e.insert("XDG_CONFIG_HOME", m_tmp.path() + "/cfg");
        e.insert("XDG_STATE_HOME", m_tmp.path() + "/state");
        e.insert("XDG_CACHE_HOME", m_tmp.path() + "/cache");
        e.insert("XDG_DATA_HOME", m_tmp.path() + "/none");
        e.insert("XDG_DATA_DIRS", m_tmp.path() + "/share");
        e.insert("QT_QPA_PLATFORM", "offscreen");
        e.insert("LC_ALL", "C");
        p.setProcessEnvironment(e);
        p.start(TAOS_SEARCH_BIN, args);
        if (!p.waitForFinished(15000)) { p.kill(); return { -1, {}, "timeout" }; }
        return { p.exitCode(), QString::fromUtf8(p.readAllStandardOutput()), QString::fromUtf8(p.readAllStandardError()) };
    }
private slots:
    void initTestCase() {
        QDir().mkpath(m_tmp.path() + "/share/applications");
        QDir().mkpath(m_tmp.path() + "/home");
        const QString src = QStringLiteral(TAOS_LAUNCHER_FIXTURES) + "/menu-apps/";
        for (const QString &f : QDir(src).entryList({ "*.desktop" })) QFile::copy(src + f, m_tmp.path() + "/share/applications/" + f);
    }
    void menuJsonHasEverySection() {
        const R r = run({ "menu", "--json" });
        QCOMPARE(r.rc, 0);
        const QJsonObject o = QJsonDocument::fromJson(r.out.toUtf8()).object();
        QVERIFY2(!o.isEmpty(), qPrintable(r.out + r.err));
        QVERIFY(o.value("count").toInt() >= 28);
        QVERIFY(o.value("categories").toArray().size() >= 8);
        QVERIFY(o.value("letters").toArray().size() >= 8);
        QVERIFY(o.value("pinned").toArray().size() >= 5);
    }
    void menuTextListsByCategory() {
        const R r = run({ "menu" });
        QCOMPARE(r.rc, 0);
        QVERIFY(r.out.contains("Internet"));
        QVERIFY(r.out.contains("Blender"));
        QVERIFY(r.out.contains("My apps"));   // the app group shows beside the app
        QVERIFY(!r.out.contains("Hidden Thing"));
    }
    void menuPartAndLetters() {
        const R r = run({ "menu", "pinned", "--json" });
        QCOMPARE(r.rc, 0);
        QVERIFY(QJsonDocument::fromJson(r.out.toUtf8()).isArray());
        QCOMPARE(run({ "menu", "nonsense" }).rc, 2);
    }
    void pinUnpinAndMoveAreSavedAndReported() {
        QCOMPARE(run({ "pin", "gimp" }).rc, 0);
        R r = run({ "menu", "pinned", "--json" });
        QVERIFY(r.out.contains("gimp.desktop"));
        QCOMPARE(run({ "move-pin", "gimp", "0" }).rc, 0);
        r = run({ "menu", "pinned", "--json" });
        QCOMPARE(QJsonDocument::fromJson(r.out.toUtf8()).array().first().toObject().value("id").toString(), QString("gimp.desktop"));
        QCOMPARE(run({ "unpin", "gimp" }).rc, 0);
        r = run({ "unpin", "gimp" });
        QCOMPARE(r.rc, 1);
        QVERIFY(r.err.contains("not pinned"));
        r = run({ "pin", "nope" });
        QCOMPARE(r.rc, 1);
        QVERIFY(r.err.contains("no application 'nope'"));
        QCOMPARE(run({ "pin" }).rc, 2);
    }
    void actionsDrillAndConfirmRule() {
        R r = run({ "actions", "--json" });
        QCOMPARE(r.rc, 0);
        QVERIFY(QJsonDocument::fromJson(r.out.toUtf8()).isArray());
        r = run({ "actions", "system", "--json" });
        QCOMPARE(r.rc, 0);
        r = run({ "actions", "nope" });
        QCOMPARE(r.rc, 1);
        r = run({ "run", "system/shutdown" });
        QCOMPARE(r.rc, 1);                        // power needs the word confirm: an agent says it on purpose
        QVERIFY(r.err.contains("confirm"));
        QCOMPARE(run({ "run", "no/such" }).rc, 1);
    }
};
QTEST_MAIN(TestCli)
#include "launcher_test_cli.moc"
