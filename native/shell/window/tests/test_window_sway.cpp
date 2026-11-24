// SPDX-License-Identifier: AGPL-3.0-or-later
// Integration: the real taos-window against a real (headless) sway, with real Wayland client windows.
// Needs a sway binary: $TAOS_TEST_SWAY, else `sway` on PATH ($TAOS_TEST_SWAY_LIBS adds library directories for
// an unpacked one). Without sway the test is skipped with a message (exit status 77).
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>
#include <csignal>

#include "core/swayconn.h"
#include "core/windowmodel.h"
#include "core/windowops.h"

using namespace TaosWin;

class TestWindowSway : public QObject {
    Q_OBJECT
    QTemporaryDir m_dir;
    QProcessEnvironment m_env;
    QProcess m_sway;
    QString m_sock, m_swayBin;
    QList<QProcess *> m_clients;
    bool m_ready = false;

    // ---- helpers -----------------------------------------------------------------------------------
    QString runCli(const QStringList &args, int *code = nullptr, int timeoutMs = 10000) {
        QProcess p;
        p.setProcessEnvironment(m_env);
        p.start(QStringLiteral(TAOS_WINDOW_BIN), args);
        if (!p.waitForFinished(timeoutMs)) { p.kill(); p.waitForFinished(); if (code) *code = -1; return QStringLiteral("TIMEOUT"); }
        if (code) *code = p.exitCode();
        return QString::fromUtf8(p.readAllStandardOutput()) + QString::fromUtf8(p.readAllStandardError());
    }
    bool cli(const QStringList &args) {
        int code = -1;
        const QString out = runCli(args, &code);
        if (code != 0) qWarning("taos-window %s -> %d: %s", qPrintable(args.join(QLatin1Char(' '))), code, qPrintable(out));
        return code == 0;
    }
    QJsonArray listJson() {
        int code = -1;
        QProcess p;
        p.setProcessEnvironment(m_env);
        p.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("list"), QStringLiteral("--json")});
        p.waitForFinished(10000);
        code = p.exitCode();
        if (code != 0) return {};
        return QJsonDocument::fromJson(p.readAllStandardOutput()).array();
    }
    QJsonObject win(int id) {
        for (const QJsonValue &v : listJson()) if (v.toObject().value(QLatin1String("id")).toInt() == id) return v.toObject();
        return {};
    }
    QJsonObject winByApp(const QString &app) {
        for (const QJsonValue &v : listJson()) if (v.toObject().value(QLatin1String("app_id")).toString() == app) return v.toObject();
        return {};
    }
    template <class F> bool waitFor(F f, int ms = 8000) {
        QElapsedTimer t; t.start();
        while (t.elapsed() < ms) { if (f()) return true; QTest::qWait(80); }
        return f();
    }
    // The raw tree, straight from sway (not through taos-window).
    Tree rawTree() {
        SwayConnection c; QByteArray j; Tree t;
        if (c.open(m_sock) && c.request(Ipc::GetTree, {}, &j)) parseTree(j, &t);
        return t;
    }
    QByteArray swaymsg(const QString &cmd) {
        SwayConnection c; QByteArray r;
        if (c.open(m_sock)) c.request(Ipc::RunCommand, cmd.toUtf8(), &r);
        return r;
    }
    QProcess *startClient(const QString &appId, const QString &title) {
        auto *p = new QProcess(this);
        QProcessEnvironment e = m_env;
        e.insert(QStringLiteral("QT_QPA_PLATFORM"), QStringLiteral("wayland"));
        e.insert(QStringLiteral("WAYLAND_DISPLAY"), QStringLiteral("wayland-1"));
        p->setProcessEnvironment(e);
        p->start(QStringLiteral(TAOS_WINDOW_DUMMY), {appId, title});
        m_clients << p;
        return p;
    }

private slots:
    void initTestCase() {
        QString sway = qEnvironmentVariable("TAOS_TEST_SWAY");
        if (sway.isEmpty()) sway = QStandardPaths::findExecutable(QStringLiteral("sway"));
        if (sway.isEmpty() || !QFile::exists(sway))
            QSKIP("SKIPPED: no sway binary (set TAOS_TEST_SWAY or install sway); the integration test needs a real compositor");
        m_swayBin = sway;
        QVERIFY(m_dir.isValid());
        const QString rt = m_dir.path() + QStringLiteral("/rt");
        QVERIFY(QDir().mkpath(rt));
        QFile::setPermissions(rt, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        m_env = QProcessEnvironment::systemEnvironment();
        m_env.insert(QStringLiteral("XDG_RUNTIME_DIR"), rt);
        m_env.insert(QStringLiteral("WLR_BACKENDS"), QStringLiteral("headless"));
        m_env.insert(QStringLiteral("WLR_RENDERER"), QStringLiteral("pixman"));
        m_env.insert(QStringLiteral("WLR_LIBINPUT_NO_DEVICES"), QStringLiteral("1"));
        m_env.insert(QStringLiteral("TAOS_DESKTOP_CONFIG"), m_dir.path() + QStringLiteral("/desktop.json"));
        m_env.insert(QStringLiteral("LC_ALL"), QStringLiteral("C.UTF-8"));
        m_env.remove(QStringLiteral("WAYLAND_DISPLAY"));
        m_env.remove(QStringLiteral("SWAYSOCK"));
        const QString libs = qEnvironmentVariable("TAOS_TEST_SWAY_LIBS");
        if (!libs.isEmpty())
            m_env.insert(QStringLiteral("LD_LIBRARY_PATH"), libs + QLatin1Char(':') + m_env.value(QStringLiteral("LD_LIBRARY_PATH")));
        // The config: what the session ships (border pixel) plus the real snippet, validated by sway itself.
        const QString cfg = m_dir.path() + QStringLiteral("/config");
        QFile f(cfg);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("default_border pixel 1\ndefault_floating_border pixel 1\n");
        f.write(QByteArray("include ") + QStringLiteral(TAOS_WINDOW_SNIPPET).toUtf8() + "\n");
        f.close();
        QProcess check;
        check.setProcessEnvironment(m_env);
        check.start(sway, {QStringLiteral("-C"), QStringLiteral("-c"), cfg});
        QVERIFY(check.waitForFinished(15000));
        const QString checkOut = QString::fromUtf8(check.readAllStandardOutput() + check.readAllStandardError());
        QVERIFY2(check.exitCode() == 0, qPrintable(QStringLiteral("sway rejected 46-taos-window.conf: ") + checkOut));
        QVERIFY2(!checkOut.contains(QLatin1String("rror")), qPrintable(checkOut));

        m_sway.setProcessEnvironment(m_env);
        m_sway.start(sway, {QStringLiteral("-c"), cfg});
        QVERIFY2(m_sway.waitForStarted(), "sway did not start");
        QVERIFY(waitFor([&] {
            const QStringList socks = QDir(rt).entryList({QStringLiteral("sway-ipc.*.sock")}, QDir::System | QDir::Files);
            if (socks.isEmpty() || !QFile::exists(rt + QStringLiteral("/wayland-1"))) return false;
            m_sock = rt + QLatin1Char('/') + socks.first();
            return true;
        }, 15000));
        m_env.insert(QStringLiteral("SWAYSOCK"), m_sock);
        // taos-window needs `swaymsg`-free access only, but Qt clients need the compositor up.
        m_ready = true;
    }

    void cleanupTestCase() {
        for (QProcess *p : std::as_const(m_clients)) { p->terminate(); if (!p->waitForFinished(2000)) p->kill(); }
        if (m_sway.state() != QProcess::NotRunning) { m_sway.terminate(); if (!m_sway.waitForFinished(4000)) m_sway.kill(); }
    }

    void listSeesTwoWindows() {
        QVERIFY(m_ready);
        QVERIFY2(listJson().isEmpty(), "no windows yet");
        QProcess *a = startClient(QStringLiteral("taos.test.one"), QStringLiteral("One"));
        QVERIFY(waitFor([&] { return !winByApp(QStringLiteral("taos.test.one")).isEmpty(); }));
        QProcess *b = startClient(QStringLiteral("taos.test.two"), QStringLiteral("Two"));
        QVERIFY(waitFor([&] { return !winByApp(QStringLiteral("taos.test.two")).isEmpty(); }));
        Q_UNUSED(a) Q_UNUSED(b)
        const QJsonObject one = winByApp(QStringLiteral("taos.test.one"));
        const QJsonObject two = winByApp(QStringLiteral("taos.test.two"));
        QCOMPARE(one.value(QLatin1String("title")).toString(), QStringLiteral("One"));
        QVERIFY(one.value(QLatin1String("pid")).toInt() > 0);
        QCOMPARE(one.value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QVERIFY(!one.value(QLatin1String("minimised")).toBool());
        QVERIFY(!one.value(QLatin1String("floating")).toBool());
        QCOMPARE(one.value(QLatin1String("output")).toString(), QStringLiteral("HEADLESS-1"));
        QStringList keys = one.keys();
        keys.sort();
        QCOMPARE(keys, QStringList({"app_id", "floating", "focused", "fullscreen", "id", "minimised", "output", "pid", "title", "workspace"}));
        QVERIFY(two.value(QLatin1String("focused")).toBool() != one.value(QLatin1String("focused")).toBool());
        // plain-text list works too
        int code = -1;
        QVERIFY(runCli({QStringLiteral("list")}, &code).contains(QLatin1String("taos.test.two")));
        QCOMPARE(code, 0);
        // Make the second window floating at a known place.
        const int idTwo = two.value(QLatin1String("id")).toInt();
        swaymsg(QStringLiteral("[con_id=%1] floating enable, resize set 400 300, move position 200 150").arg(idTwo));
        QVERIFY(waitFor([&] { return win(idTwo).value(QLatin1String("floating")).toBool(); }));
        const Tree t = rawTree();
        QCOMPARE(t.find(idTwo)->rect, QRect(200, 150, 400, 300));
    }

    void minimiseAndRestoreTiled() {
        const int idOne = winByApp(QStringLiteral("taos.test.one")).value(QLatin1String("id")).toInt();
        QVERIFY(idOne > 0);
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("minimised")).toBool(); }));
        // Still listed, still its workspace and tiled-ness; and the state lives in sway as marks.
        QJsonObject w = win(idOne);
        QCOMPARE(w.value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QCOMPARE(w.value(QLatin1String("output")).toString(), QStringLiteral("HEADLESS-1"));
        QVERIFY(!w.value(QLatin1String("floating")).toBool());
        const Tree t = rawTree();
        const Window *raw = t.find(idOne);
        QVERIFY(raw && raw->inScratchpad);
        QVERIFY2(raw->marks.contains(QStringLiteral("taos_min_%1").arg(idOne)), qPrintable(raw->marks.join(QLatin1Char(','))));
        QVERIFY(raw->marks.contains(QStringLiteral("taos_ws_1")));
        // Hidden: not part of any visible workspace.
        // Minimising it again changes nothing.
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(win(idOne).value(QLatin1String("minimised")).toBool());
        // Go to another workspace, then restore from there: it returns to workspace 1.
        swaymsg(QStringLiteral("workspace 2"));
        QVERIFY(waitFor([&] { return rawTree().focusedWorkspace == QStringLiteral("2"); }));
        QVERIFY(cli({QStringLiteral("restore"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("minimised")).toBool(); }));
        w = win(idOne);
        QCOMPARE(w.value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QVERIFY(!w.value(QLatin1String("floating")).toBool());
        QVERIFY(w.value(QLatin1String("focused")).toBool());
        QCOMPARE(rawTree().focusedWorkspace, QStringLiteral("1"));
        const Window *back = rawTree().find(idOne);
        QVERIFY(back && !back->inScratchpad);
        for (const QString &m : back->marks) QVERIFY2(!isOurMark(m), qPrintable(m));   // marks cleaned up
        QVERIFY(!back->fullscreen);
    }

    void minimiseAndRestoreFloating() {
        const int idTwo = winByApp(QStringLiteral("taos.test.two")).value(QLatin1String("id")).toInt();
        const QRect before = rawTree().find(idTwo)->rect;
        QCOMPARE(before, QRect(200, 150, 400, 300));
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(win(idTwo).value(QLatin1String("floating")).toBool());   // reported as it will come back
        QVERIFY(cli({QStringLiteral("restore"), QStringLiteral("last")}));
        QVERIFY(waitFor([&] { return !win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(win(idTwo).value(QLatin1String("floating")).toBool());
        QCOMPARE(win(idTwo).value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QCOMPARE(rawTree().find(idTwo)->rect, before);   // same place, same size
    }

    void restoreLastOrdersByTime() {
        const int idOne = winByApp(QStringLiteral("taos.test.one")).value(QLatin1String("id")).toInt();
        const int idTwo = winByApp(QStringLiteral("taos.test.two")).value(QLatin1String("id")).toInt();
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idTwo)}));
        QTest::qWait(30);
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("minimised")).toBool() && win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        // Nothing is stranded: both are listed.
        QCOMPARE(listJson().size(), 2);
        QVERIFY(cli({QStringLiteral("restore"), QStringLiteral("last")}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(win(idTwo).value(QLatin1String("minimised")).toBool());   // the earlier one is still minimised
        // focus restores a minimised window.
        QVERIFY(cli({QStringLiteral("focus"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return !win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(win(idTwo).value(QLatin1String("focused")).toBool());
        // toggle-minimise on the focused window minimises it, and again restores it.
        QVERIFY(cli({QStringLiteral("toggle-minimise"), QStringLiteral("focused")}));
        QVERIFY(waitFor([&] { return win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("toggle-minimise"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return !win(idTwo).value(QLatin1String("minimised")).toBool(); }));
    }

    void fullSizeTiledReverts() {
        const int idOne = winByApp(QStringLiteral("taos.test.one")).value(QLatin1String("id")).toInt();
        const Tree t0 = rawTree();
        const QRect usable = t0.workspace(QStringLiteral("1"))->rect;
        QVERIFY(!t0.find(idOne)->floating);
        QVERIFY(cli({QStringLiteral("full"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("floating")).toBool(); }));
        const Tree t1 = rawTree();
        QCOMPARE(t1.find(idOne)->rect, usable);
        QVERIFY(t1.find(idOne)->fullSize);
        QVERIFY(!t1.find(idOne)->fullscreen);   // not sway's fullscreen: the bar stays
        QVERIFY(cli({QStringLiteral("full"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("floating")).toBool(); }));
        const Tree t2 = rawTree();
        QVERIFY(!t2.find(idOne)->fullSize);
        for (const QString &m : t2.find(idOne)->marks) QVERIFY2(!isOurMark(m), qPrintable(m));
    }

    void fullSizeFloatingReverts() {
        const int idTwo = winByApp(QStringLiteral("taos.test.two")).value(QLatin1String("id")).toInt();
        const Tree t0 = rawTree();
        QVERIFY(t0.find(idTwo)->floating);
        const QRect before = t0.find(idTwo)->rect;
        const QRect usable = t0.workspace(QStringLiteral("1"))->rect;
        QVERIFY(cli({QStringLiteral("full"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return rawTree().find(idTwo)->rect == usable; }));
        QVERIFY(cli({QStringLiteral("full"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return rawTree().find(idTwo)->rect == before; }));
        QVERIFY(win(idTwo).value(QLatin1String("floating")).toBool());
    }

    void fullSizeSurvivesMinimise() {
        const int idTwo = winByApp(QStringLiteral("taos.test.two")).value(QLatin1String("id")).toInt();
        const QRect usable = rawTree().workspace(QStringLiteral("1"))->rect;
        QVERIFY(cli({QStringLiteral("full"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return rawTree().find(idTwo)->rect == usable; }));
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("restore"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return !win(idTwo).value(QLatin1String("minimised")).toBool(); }));
        QCOMPARE(rawTree().find(idTwo)->rect, usable);
        QVERIFY(cli({QStringLiteral("full"), QString::number(idTwo)}));   // and it still reverts
        QVERIFY(waitFor([&] { return rawTree().find(idTwo)->rect == QRect(200, 150, 400, 300); }));
    }

    void stateSurvivesTheDeathOfTaosWindow() {
        const int idOne = winByApp(QStringLiteral("taos.test.one")).value(QLatin1String("id")).toInt();
        // A long-lived `subscribe` is running while we minimise, and is then killed with SIGKILL.
        QProcess sub;
        sub.setProcessEnvironment(m_env);
        sub.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("subscribe")});
        QVERIFY(sub.waitForStarted());
        QTest::qWait(400);
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("restore"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QTest::qWait(500);
        const QString lines = QString::fromUtf8(sub.readAllStandardOutput());
        sub.kill();   // SIGKILL
        sub.waitForFinished(3000);
        QVERIFY2(lines.contains(QLatin1String("\"event\":\"minimise\"")), qPrintable(lines));
        QVERIFY2(lines.contains(QLatin1String("\"event\":\"restore\"")), qPrintable(lines));
        QVERIFY2(lines.contains(QLatin1String("\"app_id\":\"taos.test.one\"")), qPrintable(lines));
        // The taos-window processes are all gone; the window is still minimised, as sway holds it.
        QTest::qWait(300);
        QVERIFY(win(idOne).value(QLatin1String("minimised")).toBool());
        QVERIFY(rawTree().find(idOne)->marks.contains(QStringLiteral("taos_min_%1").arg(idOne)));
        // A brand new process restores it.
        QVERIFY(cli({QStringLiteral("restore"), QStringLiteral("last")}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("minimised")).toBool(); }));
    }

    void errorsAreSentences() {
        int code = -1;
        QString out = runCli({QStringLiteral("close"), QStringLiteral("99999")}, &code);
        QCOMPARE(code, 1);
        QVERIFY(out.contains(QLatin1String("no window with id 99999")));
        out = runCli({QStringLiteral("frobnicate"), QStringLiteral("1")}, &code);
        QCOMPARE(code, 2);
        // Not under sway: a clear message and a distinct status.
        QProcess p;
        QProcessEnvironment e = m_env;
        e.remove(QStringLiteral("SWAYSOCK"));
        e.remove(QStringLiteral("I3SOCK"));
        p.setProcessEnvironment(e);
        p.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("list"), QStringLiteral("--json")});
        QVERIFY(p.waitForFinished(5000));
        QCOMPARE(p.exitCode(), 3);
        QVERIFY(QString::fromUtf8(p.readAllStandardError()).contains(QLatin1String("not running under sway")));
    }

    void closeRemovesWindows() {
        const int idOne = winByApp(QStringLiteral("taos.test.one")).value(QLatin1String("id")).toInt();
        const int idTwo = winByApp(QStringLiteral("taos.test.two")).value(QLatin1String("id")).toInt();
        // Close a visible window.
        QVERIFY(cli({QStringLiteral("restore"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return !win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("close"), QString::number(idTwo)}));
        QVERIFY(waitFor([&] { return win(idTwo).isEmpty(); }));
        QCOMPARE(listJson().size(), 1);
        // Close a minimised window: it goes too, and does not linger in the scratchpad.
        QVERIFY(cli({QStringLiteral("minimise"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY(cli({QStringLiteral("close"), QString::number(idOne)}));
        QVERIFY(waitFor([&] { return win(idOne).isEmpty(); }));
        QVERIFY(listJson().isEmpty());
        for (QProcess *c : m_clients.mid(0, 2)) QVERIFY(c->waitForFinished(5000));
    }

    void desktopAndTilingModes() {
        // Nothing is open here: the earlier tests closed everything. No desktop.json yet: desktop is the default.
        QVERIFY(listJson().isEmpty());
        QCOMPARE(runCli({QStringLiteral("mode"), QStringLiteral("get")}).trimmed(), QStringLiteral("desktop"));
        QProcess daemon;
        daemon.setProcessEnvironment(m_env);
        daemon.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("mode-daemon")});
        QVERIFY(daemon.waitForStarted());
        QTest::qWait(500);
        const QRect usable = rawTree().workspace(QStringLiteral("1"))->rect;
        auto openWin = [&](const QString &app) -> int {
            startClient(app, app);
            if (!waitFor([&] { return !winByApp(app).isEmpty(); })) return 0;
            return winByApp(app).value(QLatin1String("id")).toInt();
        };
        // Desktop mode: a new window floats, inside the workspace, and the next one is cascaded.
        const int a = openWin(QStringLiteral("taos.mode.a"));
        QVERIFY(a > 0);
        QVERIFY(waitFor([&] { return win(a).value(QLatin1String("floating")).toBool(); }));
        QTest::qWait(300);
        const QRect ra = rawTree().find(a)->rect;
        QVERIFY2(usable.contains(ra), qPrintable(QStringLiteral("%1x%2").arg(ra.width()).arg(ra.height())));
        const int b = openWin(QStringLiteral("taos.mode.b"));
        QVERIFY(b > 0);
        QVERIFY(waitFor([&] { return win(b).value(QLatin1String("floating")).toBool(); }));
        QTest::qWait(300);
        QVERIFY(rawTree().find(b)->rect.topLeft() != ra.topLeft());
        // To tiling: both tile, on the same workspace; the choice is saved per workspace.
        QVERIFY(cli({QStringLiteral("mode"), QStringLiteral("tiling")}));
        QVERIFY(waitFor([&] { return !win(a).value(QLatin1String("floating")).toBool() && !win(b).value(QLatin1String("floating")).toBool(); }));
        QCOMPARE(win(a).value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QCOMPARE(win(b).value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QCOMPARE(runCli({QStringLiteral("mode"), QStringLiteral("get")}).trimmed(), QStringLiteral("tiling"));
        QFile cfg(m_dir.path() + QStringLiteral("/desktop.json"));
        QVERIFY(cfg.open(QIODevice::ReadOnly));
        const QJsonObject saved = QJsonDocument::fromJson(cfg.readAll()).object();
        QCOMPARE(saved.value(QLatin1String("workspaces")).toObject().value(QLatin1String("1")).toString(), QStringLiteral("tiling"));
        // A new window in tiling mode tiles.
        const int c = openWin(QStringLiteral("taos.mode.c"));
        QVERIFY(c > 0);
        QTest::qWait(700);
        QVERIFY(!win(c).value(QLatin1String("floating")).toBool());
        // Back to desktop: all three float and keep where they were on screen.
        QHash<int, QRect> tiled;
        for (int id : {a, b, c}) tiled.insert(id, rawTree().find(id)->rect);
        QVERIFY(cli({QStringLiteral("mode"), QStringLiteral("desktop")}));
        QVERIFY(waitFor([&] { for (int id : {a, b, c}) if (!win(id).value(QLatin1String("floating")).toBool()) return false; return true; }));
        QTest::qWait(300);
        for (int id : {a, b, c}) QCOMPARE(rawTree().find(id)->rect, tiled.value(id));
        // Per workspace: workspace 2 on its own, and `mode list` shows both.
        QVERIFY(cli({QStringLiteral("mode"), QStringLiteral("tiling"), QStringLiteral("--workspace"), QStringLiteral("2")}));
        QCOMPARE(runCli({QStringLiteral("mode"), QStringLiteral("get"), QStringLiteral("--workspace=2")}).trimmed(), QStringLiteral("tiling"));
        QCOMPARE(runCli({QStringLiteral("mode"), QStringLiteral("get")}).trimmed(), QStringLiteral("desktop"));
        const QString list = runCli({QStringLiteral("mode"), QStringLiteral("list"), QStringLiteral("--json")});
        QVERIFY2(list.contains(QLatin1String("\"mode\": \"desktop\"")), qPrintable(list));
        // A `mode` event reaches subscribers, whoever changed the file.
        QProcess sub;
        sub.setProcessEnvironment(m_env);
        sub.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("subscribe")});
        QVERIFY(sub.waitForStarted());
        QTest::qWait(400);
        QVERIFY(cli({QStringLiteral("mode"), QStringLiteral("toggle")}));
        QVERIFY(waitFor([&] { sub.waitForReadyRead(50); return sub.bytesAvailable() > 0 && sub.peek(sub.bytesAvailable()).contains("\"event\":\"mode\""); }, 4000));
        const QByteArray lines = sub.readAllStandardOutput();
        QVERIFY2(lines.contains("\"workspace\":\"1\"") && lines.contains("\"mode\":\"tiling\""), lines.constData());
        sub.kill();
        sub.waitForFinished(2000);
        daemon.terminate();
        daemon.waitForFinished(3000);
        for (const QString &app : {QStringLiteral("taos.mode.a"), QStringLiteral("taos.mode.b"), QStringLiteral("taos.mode.c")})
            cli({QStringLiteral("close"), QString::number(winByApp(app).value(QLatin1String("id")).toInt())});
        QVERIFY(waitFor([&] { return listJson().isEmpty(); }));
    }

    void foreignMinimiseIsAdopted() {
        // What swayfx's scratchpad_minimize does for set_minimized: the window is parked without our marks. The
        // helper adds them from what it saw just before, so restore still goes home, tiled.
        QVERIFY(cli({QStringLiteral("mode"), QStringLiteral("tiling")}));
        QProcess daemon;
        daemon.setProcessEnvironment(m_env);
        daemon.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("mode-daemon")});
        QVERIFY(daemon.waitForStarted());
        QTest::qWait(500);
        startClient(QStringLiteral("taos.foreign"), QStringLiteral("Foreign"));
        QVERIFY(waitFor([&] { return !winByApp(QStringLiteral("taos.foreign")).isEmpty(); }));
        const int id = winByApp(QStringLiteral("taos.foreign")).value(QLatin1String("id")).toInt();
        QVERIFY(!win(id).value(QLatin1String("floating")).toBool());
        swaymsg(QStringLiteral("[con_id=%1] move scratchpad").arg(id));
        QVERIFY(waitFor([&] { return win(id).value(QLatin1String("minimised")).toBool(); }));
        QVERIFY2(waitFor([&] { const Window *w = rawTree().find(id); return w && w->taosMinimised; }), "marks were not adopted");
        QCOMPARE(win(id).value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QVERIFY(!win(id).value(QLatin1String("floating")).toBool());   // it will come back tiled
        swaymsg(QStringLiteral("workspace 2"));
        QVERIFY(cli({QStringLiteral("restore"), QString::number(id)}));
        QVERIFY(waitFor([&] { return !win(id).value(QLatin1String("minimised")).toBool(); }));
        QCOMPARE(win(id).value(QLatin1String("workspace")).toString(), QStringLiteral("1"));
        QVERIFY(!win(id).value(QLatin1String("floating")).toBool());
        // Shown again by hand (scratchpad show) on another workspace: the stale marks are cleaned up.
        swaymsg(QStringLiteral("[con_id=%1] move scratchpad").arg(id));
        QVERIFY(waitFor([&] { const Window *w = rawTree().find(id); return w && w->taosMinimised; }));
        swaymsg(QStringLiteral("[con_id=%1] scratchpad show").arg(id));
        QVERIFY(waitFor([&] { const Window *w = rawTree().find(id); return w && !w->inScratchpad; }));
        QVERIFY(waitFor([&] { const Window *w = rawTree().find(id); if (!w) return false; for (const QString &m : w->marks) if (m.startsWith(QLatin1String("taos_min_"))) return false; return true; }));
        daemon.terminate();
        daemon.waitForFinished(3000);
        cli({QStringLiteral("close"), QString::number(id)});
        QVERIFY(waitFor([&] { return listJson().isEmpty(); }));
    }
};

QTEST_GUILESS_MAIN(TestWindowSway)
#include "test_window_sway.moc"
