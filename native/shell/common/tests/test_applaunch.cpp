// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QProcess>
#include <QtTest>

#include "applaunch.h"

class TestAppLaunch : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    static QProcessEnvironment leaky() {
        QProcessEnvironment e;
        e.insert("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");
        e.insert("QT_QPA_PLATFORM", "wayland");
        e.insert("TAOS_LAUNCHER_OWN_ENV", "QT_QPA_PLATFORM,QT_WAYLAND_SHELL_INTEGRATION");
        e.insert("TAOS_SHELL_COMPONENTS", "taos-bar taos-dock");
        e.insert("TAOS_SHELL_ARGS_TAOS_BAR", "--height 48");
        e.insert("TAOS_SHELL_SESSION_PID", "123");
        e.insert("TAOS_SHELL_BIN_DIR", "/opt/x/bin");
        e.insert("TAOS_SHELL_SLEEP_CMD", "sleep");
        e.insert("TAOS_SHELL_ENV_FILE", "/etc/taos/shell.env");     // an app wrapper sources it: kept
        e.insert("WAYLAND_DISPLAY", "wayland-9");
        e.insert("XDG_RUNTIME_DIR", "/run/user/1");
        e.insert("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus");
        e.insert("LANG", "de_DE.UTF-8");
        e.insert("GTK_THEME", "Adwaita:dark");
        e.insert("QT_PLUGIN_PATH", "/opt/taos/qt/plugins");
        e.insert("LD_LIBRARY_PATH", "/opt/taos/qt/lib");
        e.insert("PATH", "/usr/bin:/bin");
        return e;
    }
    QString run(const QString &script, const QProcessEnvironment &base, bool scope = false) {
        const QString out = m_tmp.path() + "/out.txt";
        QFile::remove(out);
        taos::LaunchOptions o;
        o.base = &base;
        o.useScope = scope;
        o.workingDirectory = m_tmp.path();
        QString err;
        if (!taos::launchApp("sh", { "-c", script, "sh", out }, &err, o)) { qWarning() << err; return {}; }
        for (int i = 0; i < 100 && !(QFile::exists(out) && QFileInfo(out).size() > 0); ++i) QTest::qWait(50);
        QTest::qWait(100);
        QFile f(out);
        f.open(QIODevice::ReadOnly);
        return QString::fromUtf8(f.readAll());
    }
private slots:
    void shellOnlyVariablesAreStripped() {
        const QProcessEnvironment c = taos::cleanAppEnvironment(leaky());
        QVERIFY2(!c.contains("QT_WAYLAND_SHELL_INTEGRATION"), "the layer-shell integration must never reach an app");
        QVERIFY(!c.contains("TAOS_LAUNCHER_OWN_ENV"));
        QVERIFY(!c.contains("TAOS_SHELL_COMPONENTS"));
        QVERIFY(!c.contains("TAOS_SHELL_ARGS_TAOS_BAR"));
        QVERIFY(!c.contains("TAOS_SHELL_SESSION_PID"));
        QVERIFY(!c.contains("TAOS_SHELL_BIN_DIR"));
        QVERIFY(!c.contains("TAOS_SHELL_SLEEP_CMD"));
    }
    void whatTheComponentSetForItselfIsStripped() {
        // QT_QPA_PLATFORM was set by the component (it is in the list it published), so it is not the person's choice
        QVERIFY(!taos::cleanAppEnvironment(leaky()).contains("QT_QPA_PLATFORM"));
        QProcessEnvironment mine = leaky();
        mine.remove("TAOS_LAUNCHER_OWN_ENV");        // the person set it: kept
        QCOMPARE(taos::cleanAppEnvironment(mine).value("QT_QPA_PLATFORM"), QString("wayland"));
    }
    void theSessionsEnvironmentIsKept() {
        const QProcessEnvironment c = taos::cleanAppEnvironment(leaky());
        QCOMPARE(c.value("WAYLAND_DISPLAY"), QString("wayland-9"));
        QCOMPARE(c.value("XDG_RUNTIME_DIR"), QString("/run/user/1"));
        QCOMPARE(c.value("DBUS_SESSION_BUS_ADDRESS"), QString("unix:path=/run/user/1/bus"));
        QCOMPARE(c.value("LANG"), QString("de_DE.UTF-8"));
        QCOMPARE(c.value("GTK_THEME"), QString("Adwaita:dark"));
        QCOMPARE(c.value("QT_PLUGIN_PATH"), QString("/opt/taos/qt/plugins"));     // the interim Qt prefix the apps need
        QCOMPARE(c.value("LD_LIBRARY_PATH"), QString("/opt/taos/qt/lib"));
        QCOMPARE(c.value("TAOS_SHELL_ENV_FILE"), QString("/etc/taos/shell.env"));
        QCOMPARE(c.value("PATH"), QString("/usr/bin:/bin"));
    }
    void aLaunchedAppsRealEnvironHasNoLayerShell() {
        const QString env = run("env > \"$1\"", leaky());
        QVERIFY2(!env.isEmpty(), "the app did not run");
        QVERIFY2(!env.contains("QT_WAYLAND_SHELL_INTEGRATION"), qPrintable(env));
        QVERIFY(!env.contains("TAOS_SHELL_COMPONENTS"));
        QVERIFY(!env.contains("TAOS_LAUNCHER_OWN_ENV"));
        QVERIFY(env.contains("WAYLAND_DISPLAY=wayland-9"));
        QVERIFY(env.contains("LANG=de_DE.UTF-8"));
    }
    void anAppIsNotAChildOfTheComponent() {
        const QString ppid = run("echo $PPID > \"$1\"", leaky()).trimmed();
        QVERIFY(!ppid.isEmpty());
        QVERIFY(ppid.toLongLong() != QCoreApplication::applicationPid());
    }
    void anAppRunsInItsOwnScopeWhenSystemdHasAUserManager() {
        QProcess probe;
        probe.start("systemd-run", { "--user", "--scope", "--quiet", "--collect", "true" });
        if (!probe.waitForFinished(5000) || probe.exitCode() != 0) QSKIP("no systemd user manager in this environment");
        const QString cg = run("cat /proc/self/cgroup > \"$1\"", QProcessEnvironment::systemEnvironment(), true);
        QVERIFY2(cg.contains("taos-app-"), qPrintable(cg));
    }
    void aMissingProgramIsReportedNotSwallowed() {
        QString err;
        QVERIFY(!taos::launchApp("definitely-not-installed-xyz", {}, &err));
        QVERIFY(err.contains("definitely-not-installed-xyz"));
    }
};
QTEST_MAIN(TestAppLaunch)
#include "test_applaunch.moc"
