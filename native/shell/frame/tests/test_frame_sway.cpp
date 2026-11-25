// SPDX-License-Identifier: AGPL-3.0-or-later
// Integration: taos-frame against a real headless sway with real foreign windows (foot, app_id chrome-...). Proves what
// can be proven without the real compositor and a layer-shell client: the daemon frames the window (sway reserves a
// 32 px title bar), reports the strip where sway's title bar corner is, follows moves, hides on fullscreen, workspace
// change and minimise, follows the config file and the appearance setting, and the actions hit the right window.
// Not covered here: the layer-shell surface itself (the build uses a compile-only stand-in for LayerShellQt, and the
// test runs the daemon on Qt's offscreen platform), see the README.
// Skipped when there is no sway or foot. Environment: TAOS_TEST_SWAY (sway binary), TAOS_TEST_SWAY_LIBS.
#include <QDir>
#include <QElapsedTimer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>
#include <functional>

#ifndef TAOS_FRAME_BIN
#error TAOS_FRAME_BIN
#endif

class FrameSway : public QObject {
    Q_OBJECT
    QTemporaryDir m_dir;
    QProcess m_sway, m_frame;
    QVector<QProcess *> m_clients;
    QString m_run, m_sock, m_display, m_frameSock, m_cfgDir;
    QProcessEnvironment m_env;

    QString swayBin() const
    {
        const QString e = qEnvironmentVariable("TAOS_TEST_SWAY");
        return e.isEmpty() ? QStandardPaths::findExecutable(QStringLiteral("sway")) : e;
    }
    QByteArray swaymsg(const QStringList &args, int timeout = 5000)
    {
        QProcess p;
        p.setProcessEnvironment(m_env);
        p.start(QStandardPaths::findExecutable(QStringLiteral("swaymsg")), args);
        if (!p.waitForFinished(timeout))
            return {};
        return p.readAllStandardOutput();
    }
    QJsonObject tree() { return QJsonDocument::fromJson(swaymsg({QStringLiteral("-t"), QStringLiteral("get_tree")})).object(); }
    static void collect(const QJsonObject &n, QVector<QJsonObject> *out)
    {
        if (n.contains(QLatin1String("shell")))
            out->append(n);
        for (const QJsonValue &c : n.value(QLatin1String("nodes")).toArray()) collect(c.toObject(), out);
        for (const QJsonValue &c : n.value(QLatin1String("floating_nodes")).toArray()) collect(c.toObject(), out);
    }
    QJsonObject swayWindow(const QString &title)
    {
        QVector<QJsonObject> all;
        collect(tree(), &all);
        for (const QJsonObject &o : all)
            if (o.value(QLatin1String("name")).toString() == title)
                return o;
        return {};
    }
    // The title bar as drawn: directly above the content rectangle (deco_rect's position is not trusted, see frameplan.cpp).
    QRect barOf(const QString &title)
    {
        const QJsonObject w = swayWindow(title);
        const QJsonObject r = w.value(QLatin1String("rect")).toObject();
        const int h = w.value(QLatin1String("deco_rect")).toObject().value(QLatin1String("height")).toInt();
        return QRect(r.value(QLatin1String("x")).toInt(), r.value(QLatin1String("y")).toInt() - h, r.value(QLatin1String("width")).toInt(), h);
    }
    int idOf(const QString &title) { return swayWindow(title).value(QLatin1String("id")).toInt(); }
    QJsonObject frameState()
    {
        QProcess p;
        QProcessEnvironment e = m_env;
        e.insert(QStringLiteral("TAOS_FRAME_SOCK"), m_frameSock);
        p.setProcessEnvironment(e);
        p.start(QStringLiteral(TAOS_FRAME_BIN), {QStringLiteral("state")});
        if (!p.waitForFinished(4000))
            return {};
        return QJsonDocument::fromJson(p.readAllStandardOutput()).object();
    }
    QJsonObject strip(int id)
    {
        for (const QJsonValue &v : frameState().value(QLatin1String("strips")).toArray())
            if (v.toObject().value(QLatin1String("id")).toInt() == id)
                return v.toObject();
        return {};
    }
    bool waitFor(const std::function<bool()> &pred, int ms = 8000, int step = 40)
    {
        QElapsedTimer t;
        t.start();
        while (t.elapsed() < ms) {
            if (pred()) return true;
            QTest::qWait(step);
        }
        return pred();
    }
    QProcess *foot(const QString &appId, const QString &title, const QString &config = QStringLiteral("/dev/null"))
    {
        auto *p = new QProcess(this);
        p->setProcessEnvironment(m_env);
        p->start(QStandardPaths::findExecutable(QStringLiteral("foot")),
                 {QStringLiteral("-c"), config, QStringLiteral("-a"), appId, QStringLiteral("-T"), title, QStringLiteral("sleep"), QStringLiteral("1000")});
        m_clients.append(p);
        return p;
    }
    void writeFile(const QString &path, const QByteArray &data)
    {
        QDir().mkpath(QFileInfo(path).absolutePath());
        QFile f(path);
        QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
        f.write(data);
    }

private slots:
    void initTestCase()
    {
        if (swayBin().isEmpty() || QStandardPaths::findExecutable(QStringLiteral("foot")).isEmpty()
            || QStandardPaths::findExecutable(QStringLiteral("swaymsg")).isEmpty())
            QSKIP("no sway, swaymsg or foot binary");
        QVERIFY(m_dir.isValid());
        m_run = m_dir.path() + QStringLiteral("/run");
        m_cfgDir = m_dir.path() + QStringLiteral("/config");
        QDir().mkpath(m_run);
        QFile::setPermissions(m_run, QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
        writeFile(m_dir.path() + QStringLiteral("/sway.conf"),
                  "default_border pixel 1\noutput HEADLESS-1 resolution 1280x720\ninclude " TAOS_FRAME_SNIPPET "\n");
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert(QStringLiteral("XDG_RUNTIME_DIR"), m_run);
        env.insert(QStringLiteral("WLR_BACKENDS"), QStringLiteral("headless"));
        env.insert(QStringLiteral("WLR_LIBINPUT_NO_DEVICES"), QStringLiteral("1"));
        env.insert(QStringLiteral("WLR_RENDERER"), QStringLiteral("pixman"));
        env.remove(QStringLiteral("WAYLAND_DISPLAY"));
        env.remove(QStringLiteral("SWAYSOCK"));
        const QString libs = qEnvironmentVariable("TAOS_TEST_SWAY_LIBS");
        if (!libs.isEmpty())
            env.insert(QStringLiteral("LD_LIBRARY_PATH"), libs + QLatin1Char(':') + env.value(QStringLiteral("LD_LIBRARY_PATH")));
        m_sway.setProcessEnvironment(env);
        m_sway.setProcessChannelMode(QProcess::MergedChannels);
        m_sway.start(swayBin(), {QStringLiteral("-c"), m_dir.path() + QStringLiteral("/sway.conf")});
        QVERIFY(m_sway.waitForStarted());
        QElapsedTimer t;
        t.start();
        while (t.elapsed() < 10000 && (m_sock.isEmpty() || m_display.isEmpty())) {
            for (const QString &f : QDir(m_run).entryList(QDir::System | QDir::Files | QDir::NoDotAndDotDot)) {
                if (f.startsWith(QLatin1String("sway-ipc"))) m_sock = m_run + QLatin1Char('/') + f;
                if (f.startsWith(QLatin1String("wayland-")) && !f.endsWith(QLatin1String(".lock"))) m_display = f;
            }
            QTest::qWait(50);
        }
        if (m_sock.isEmpty() || m_display.isEmpty())
            QSKIP(qPrintable(QStringLiteral("sway did not start here: ") + QString::fromLocal8Bit(m_sway.readAll().right(300))));
        m_env = env;
        m_env.insert(QStringLiteral("SWAYSOCK"), m_sock);
        m_env.insert(QStringLiteral("WAYLAND_DISPLAY"), m_display);
        m_env.insert(QStringLiteral("XDG_CONFIG_HOME"), m_cfgDir);
        m_env.insert(QStringLiteral("TAOS_APPEARANCE"), m_cfgDir + QStringLiteral("/taos/appearance.json"));
        m_env.insert(QStringLiteral("TAOS_THEME_DIR"), QStringLiteral(TAOS_FOLDER_THEMES));
        m_env.insert(QStringLiteral("TAOS_SYSTEM_APPEARANCE"), m_dir.path() + QStringLiteral("/none.json"));
        m_env.insert(QStringLiteral("TAOS_FRAME_CONFIG"), m_cfgDir + QStringLiteral("/taos/frame.json"));
        m_frameSock = m_dir.path() + QStringLiteral("/frame.sock");
        m_env.insert(QStringLiteral("TAOS_FRAME_SOCK"), m_frameSock);
        m_env.insert(QStringLiteral("QT_QPA_PLATFORM"), QStringLiteral("offscreen"));
        m_env.insert(QStringLiteral("QT_QUICK_BACKEND"), QStringLiteral("software"));
        writeFile(m_cfgDir + QStringLiteral("/taos/appearance.json"), "{\"theme\":\"graphite\",\"mode\":\"dark\"}");
        m_frame.setProcessEnvironment(m_env);
        m_frame.setProcessChannelMode(QProcess::ForwardedErrorChannel);
        m_frame.start(QStringLiteral(TAOS_FRAME_BIN), {});
        QVERIFY(m_frame.waitForStarted());
        QVERIFY2(waitFor([&] { return QFile::exists(m_frameSock); }, 8000), "taos-frame did not come up");
    }

    void cleanupTestCase()
    {
        for (QProcess *p : std::as_const(m_clients)) {
            p->kill();
            p->waitForFinished(1000);
        }
        m_frame.terminate();
        m_frame.waitForFinished(3000);
        m_sway.terminate();
        if (!m_sway.waitForFinished(4000)) {
            m_sway.kill();
            m_sway.waitForFinished(2000);
        }
    }

    // A matching window gets a 32 px title bar from sway and a strip over its corner; another app gets neither.
    void framesAMatchingWindowOnly()
    {
        foot(QStringLiteral("chrome-test.example__-Default"), QStringLiteral("Web app"));
        foot(QStringLiteral("org.mozilla.Test"), QStringLiteral("Plain terminal"));    // a header bar app: self decorating list
        foot(QStringLiteral("foot"), QStringLiteral("Terminal"));                     // the terminal: framed like any other window
        QVERIFY(waitFor([&] { return idOf(QStringLiteral("Web app")) && idOf(QStringLiteral("Plain terminal")) && idOf(QStringLiteral("Terminal")); }));
        const int id = idOf(QStringLiteral("Web app"));
        // sway reserves the bar, and the tuner brings it to 32 px whatever the installed font measures
        QVERIFY2(waitFor([&] { return swayWindow(QStringLiteral("Web app")).value(QLatin1String("deco_rect")).toObject().value(QLatin1String("height")).toInt() == 32; }, 12000),
                 qPrintable(QStringLiteral("title bar height is %1").arg(swayWindow(QStringLiteral("Web app")).value(QLatin1String("deco_rect")).toObject().value(QLatin1String("height")).toInt())));
        QCOMPARE(swayWindow(QStringLiteral("Web app")).value(QLatin1String("border")).toString(), QStringLiteral("normal"));
        QCOMPARE(swayWindow(QStringLiteral("Plain terminal")).value(QLatin1String("border")).toString(), QStringLiteral("pixel"));
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }));
        QVERIFY(strip(idOf(QStringLiteral("Plain terminal"))).isEmpty());
        QVERIFY2(waitFor([&] { return swayWindow(QStringLiteral("Terminal")).value(QLatin1String("border")).toString() == QLatin1String("normal"); }),
                 "the terminal gets sway's title bar");
        QVERIFY2(waitFor([&] { return strip(idOf(QStringLiteral("Terminal"))).value(QLatin1String("show")).toBool(); }), "and the three buttons");
        // the strip is over the bar's corner: 8 px in, centred in the bar, 80 x 24
        const QRect bar = barOf(QStringLiteral("Web app"));
        const QJsonObject s = strip(id);
        QCOMPARE(s.value(QLatin1String("x")).toInt(), bar.x() + 8);
        QCOMPARE(s.value(QLatin1String("y")).toInt(), bar.y() + 4);
        QCOMPARE(s.value(QLatin1String("width")).toInt(), 80);
        QCOMPARE(s.value(QLatin1String("height")).toInt(), 24);
        QCOMPARE(s.value(QLatin1String("title")).toString(), QStringLiteral("Web app"));
    }

    void followsMovesAndMeasuresLatency()
    {
        const int id = idOf(QStringLiteral("Web app"));
        QVERIFY(id);
        swaymsg({QStringLiteral("[con_id=%1] floating enable, resize set 500 300, move absolute position 100 100").arg(id)});
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool() && strip(id).value(QLatin1String("x")).toInt() == 108 && strip(id).value(QLatin1String("height")).toInt() == 24; }));
        QList<qint64> lat;
        const QList<QPoint> targets = {QPoint(300, 200), QPoint(50, 400), QPoint(640, 100), QPoint(200, 300)};
        for (const QPoint &p : targets) {
            const qint64 before = qint64(frameState().value(QLatin1String("pos_changed_ms")).toDouble());
            swaymsg({QStringLiteral("[con_id=%1] move absolute position %2 %3").arg(id).arg(p.x()).arg(p.y())});
            const qint64 cmdDone = QDateTime::currentMSecsSinceEpoch();
            QVERIFY(waitFor([&] { return qint64(frameState().value(QLatin1String("pos_changed_ms")).toDouble()) > before; }, 4000, 5));
            const qint64 seen = qint64(frameState().value(QLatin1String("pos_changed_ms")).toDouble());
            lat << seen - cmdDone;       // negative: the daemon saw it before the command's reply reached us
            // the bar is where sway says it is (`move absolute` positions the window below its bar)
            const QRect bar = barOf(QStringLiteral("Web app"));
            QCOMPARE(bar.height(), 32);
            QCOMPARE(bar.y(), p.y() - 0);        // `move absolute position` puts the bar's top where asked
            QVERIFY2(waitFor([&] { const QJsonObject s = strip(id); return s.value(QLatin1String("x")).toInt() == bar.x() + 8 && s.value(QLatin1String("y")).toInt() == bar.y() + 4; }, 4000, 5),
                     qPrintable(QStringLiteral("strip did not follow to %1,%2").arg(p.x()).arg(p.y())));
            // while it moved the strip hid, and it came back (settled) on its own
            QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }, 2000));
        }
        qInfo() << "follow latency (ms, daemon noticed the new position minus the moment the move command returned; <= 0 means before it returned):" << lat;
        for (qint64 l : lat)
            QVERIFY2(l < 100, "the strip must follow a move within a few frames");
    }

    // An interactive move raises no sway event: dragging the title bar with a (virtual) pointer must still hide the
    // strip while it moves and bring it back, in the right place, when the window is dropped.
    void dragHidesThenFollows()
    {
#ifndef TAOS_FRAME_VPTOOL
        QSKIP("no wlr-virtual-pointer helper built (needs wayland-scanner and libwayland-client)");
#else
        const int id = idOf(QStringLiteral("Web app"));
        QRect deco = barOf(QStringLiteral("Web app"));
        const int x0 = deco.x(), y0 = deco.y();
        // press on the bar, away from the buttons (the middle), and drag 200 px right and 60 px down in steps
        const int gx = x0 + 250, gy = y0 + 16;
        // drag and release inside one helper process (the virtual pointer goes away with it)
        QProcess drag;
        drag.setProcessEnvironment(m_env);
        drag.start(QStringLiteral(TAOS_FRAME_VPTOOL),
                   {QStringLiteral("1280"), QStringLiteral("720"), QStringLiteral("move"), QString::number(gx), QString::number(gy),
                    QStringLiteral("sleep"), QStringLiteral("60"), QStringLiteral("press"), QStringLiteral("sleep"), QStringLiteral("60"),
                    QStringLiteral("drag"), QString::number(gx), QString::number(gy), QString::number(gx + 200), QString::number(gy + 60),
                    QStringLiteral("20"), QStringLiteral("40"), QStringLiteral("sleep"), QStringLiteral("1200"), QStringLiteral("release")});
        QVERIFY(drag.waitForStarted());
        QVERIFY(drag.waitForReadyRead(5000));
        const qint64 dragStart = drag.readLine().trimmed().toLongLong();       // first pointer motion
        const bool hid = waitFor([&] { return strip(id).value(QLatin1String("reason")).toString() == QLatin1String("moving"); }, 3000, 5);
        if (!hid) {
            drag.waitForFinished(10000);
            qInfo() << "vptool:" << drag.exitCode() << drag.readAllStandardError() << "bar before" << x0 << y0 << "after"
                    << barOf(QStringLiteral("Web app")) << "press at" << gx << gy;
        }
        QVERIFY2(hid, "the strip did not hide while the window was being dragged");
        const qint64 hidAt = qint64(frameState().value(QLatin1String("moving_since_ms")).toDouble());
        qInfo() << "interactive drag: first pointer motion -> strip hidden:" << (hidAt - dragStart) << "ms (idle poll 100 ms; sway starts a move only after the pointer left a small dead zone)";
        QVERIFY2(hidAt - dragStart < 400, "the strip must hide soon after an interactive drag starts");
        QVERIFY(drag.waitForFinished(10000));
        deco = barOf(QStringLiteral("Web app"));
        QVERIFY2(deco.x() > x0 + 100, "the drag did not move the window (no pointer?)");
        QVERIFY(waitFor([&] { const QJsonObject s = strip(id); return s.value(QLatin1String("show")).toBool() && s.value(QLatin1String("x")).toInt() == deco.x() + 8 && s.value(QLatin1String("y")).toInt() == deco.y() + 4; }, 3000));
#endif
    }

    void hidesOnFullscreenWorkspaceAndMinimise()
    {
        const int id = idOf(QStringLiteral("Web app"));
        swaymsg({QStringLiteral("[con_id=%1] fullscreen enable").arg(id)});
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("reason")).toString() == QLatin1String("fullscreen"); }));
        QVERIFY(!strip(id).value(QLatin1String("show")).toBool());
        swaymsg({QStringLiteral("[con_id=%1] fullscreen disable").arg(id)});
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }));
        swaymsg({QStringLiteral("workspace 2")});
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("reason")).toString() == QLatin1String("hidden"); }));
        swaymsg({QStringLiteral("workspace 1")});
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }));
    }

    void minimiseActsOnTheRightWindow()
    {
        foot(QStringLiteral("chrome-second.example__-Default"), QStringLiteral("Second app"));
        QVERIFY(waitFor([&] { return idOf(QStringLiteral("Second app")) != 0; }));
        const int a = idOf(QStringLiteral("Web app")), b = idOf(QStringLiteral("Second app"));
        QVERIFY(waitFor([&] { return strip(b).value(QLatin1String("show")).toBool() && strip(a).value(QLatin1String("show")).toBool(); }));
        QProcess p;
        p.setProcessEnvironment(m_env);
        p.start(QStringLiteral(TAOS_FRAME_BIN), {QStringLiteral("press"), QString::number(b), QStringLiteral("minimise")});
        QVERIFY(p.waitForFinished(5000));
        QCOMPARE(p.exitCode(), 0);
        QVERIFY(waitFor([&] { return strip(b).value(QLatin1String("reason")).toString() == QLatin1String("minimised"); }));
        QVERIFY(strip(a).value(QLatin1String("show")).toBool());                         // the other one is untouched
        QVERIFY(swayWindow(QStringLiteral("Web app")).value(QLatin1String("visible")).toBool());
        // restore through the engine's own CLI path: the strip comes back
        swaymsg({QStringLiteral("[con_id=%1] scratchpad show").arg(b)});
        QVERIFY(waitFor([&] { return strip(b).value(QLatin1String("reason")).toString() != QLatin1String("minimised"); }));
    }


    // "Full screen" in the window menu must be sway's own fullscreen: fullscreen_mode 1 and the container exactly the
    // output (the client is told set_fullscreen). It once resized the container to the workspace and left
    // fullscreen_mode 0, so a client that drew itself to the output ran past the bottom. Maximise stays inside the usable
    // area, title bar included, and restore gives the old geometry back.
    void fullScreenIsRealFullscreenAndMaximiseStaysInside()
    {
        const int id = idOf(QStringLiteral("Web app"));
        QVERIFY(id);
        swaymsg({QStringLiteral("[con_id=%1] floating enable, resize set 500 300, move absolute position 240 160").arg(id)});
        QTest::qWait(400);
        auto node = [&] { return swayWindow(QStringLiteral("Web app")); };
        auto rectOf = [](const QJsonValue &v) { const QJsonObject r = v.toObject();
                                                return QRect(r.value(QLatin1String("x")).toInt(), r.value(QLatin1String("y")).toInt(), r.value(QLatin1String("width")).toInt(), r.value(QLatin1String("height")).toInt()); };
        // the container: the content plus its title bar
        auto container = [&] { const QJsonObject n = node(); QRect c = rectOf(n.value(QLatin1String("rect")));
                               if (n.value(QLatin1String("fullscreen_mode")).toInt() == 0) c.setTop(c.top() - n.value(QLatin1String("deco_rect")).toObject().value(QLatin1String("height")).toInt());
                               return c; };
        auto press = [&](const QString &what, int *code = nullptr) {
            QProcess p; p.setProcessEnvironment(m_env);
            p.start(QStringLiteral(TAOS_FRAME_BIN), {QStringLiteral("press"), QString::number(id), what});
            QVERIFY(p.waitForFinished(8000));
            if (code) *code = p.exitCode();
            else QCOMPARE(p.exitCode(), 0);
        };
        auto windowCli = [&](const QString &verb) {
            QProcess p; p.setProcessEnvironment(m_env);
            p.start(QStringLiteral(TAOS_WINDOW_BIN), {verb, QString::number(id)});
            QVERIFY(p.waitForFinished(8000));
            QCOMPARE(p.exitCode(), 0);
        };
        const QRect output(0, 0, 1280, 720);
        const QRect before = container();
        // maximise: inside the usable area, not fullscreen
        press(QStringLiteral("full"));
        QVERIFY2(waitFor([&] { return container().width() == output.width(); }), "maximise did not fill the width");
        QTest::qWait(300);
        QCOMPARE(node().value(QLatin1String("fullscreen_mode")).toInt(), 0);
        QVERIFY2(output.contains(container()), qPrintable(QStringLiteral("container runs outside the area: %1,%2 %3x%4").arg(container().x()).arg(container().y()).arg(container().width()).arg(container().height())));
        // full screen: sway's state, the container is the output
        press(QStringLiteral("fullscreen"));
        QVERIFY2(waitFor([&] { return node().value(QLatin1String("fullscreen_mode")).toInt() == 1; }), "not sway's fullscreen");
        QVERIFY(waitFor([&] { return rectOf(node().value(QLatin1String("rect"))) == output; }));
        // restore: leaves full screen AND maximise, back to the geometry before
        windowCli(QStringLiteral("restore"));
        QVERIFY(waitFor([&] { return node().value(QLatin1String("fullscreen_mode")).toInt() == 0 && container().width() == before.width(); }));
        QTest::qWait(300);
        QCOMPARE(container().topLeft(), before.topLeft());
        QVERIFY2(qAbs(container().height() - before.height()) <= 16, qPrintable(QStringLiteral("height %1 vs %2").arg(container().height()).arg(before.height())));
        // a second Full screen from the menu on a window that is not maximised
        press(QStringLiteral("fullscreen"));
        QVERIFY(waitFor([&] { return node().value(QLatin1String("fullscreen_mode")).toInt() == 1 && rectOf(node().value(QLatin1String("rect"))) == output; }));
        windowCli(QStringLiteral("restore"));
        QVERIFY(waitFor([&] { return node().value(QLatin1String("fullscreen_mode")).toInt() == 0 && container().width() == before.width(); }));
        QTest::qWait(300);
        QCOMPARE(container().topLeft(), before.topLeft());
    }

    // The window core saves and replays `rect`; with a sway title bar rect is the content below the bar, and
    // `move`/`resize` talk about the container. A round trip must not walk the window.
    void fullSizeAndMinimiseRoundTripKeepTheGeometry()
    {
        const int id = idOf(QStringLiteral("Web app"));
        QVERIFY(id);
        swaymsg({QStringLiteral("[con_id=%1] floating enable, resize set 500 300, move absolute position 240 160").arg(id)});
        QTest::qWait(300);
        auto rectOfWin = [&] { const QJsonObject r = swayWindow(QStringLiteral("Web app")).value(QLatin1String("rect")).toObject();
                               return QRect(r.value(QLatin1String("x")).toInt(), r.value(QLatin1String("y")).toInt(), r.value(QLatin1String("width")).toInt(), r.value(QLatin1String("height")).toInt()); };
        auto press = [&](const QString &what) {
            QProcess p; p.setProcessEnvironment(m_env);
            p.start(QStringLiteral(TAOS_FRAME_BIN), {QStringLiteral("press"), QString::number(id), what});
            QVERIFY(p.waitForFinished(8000)); QCOMPARE(p.exitCode(), 0);
        };
        const QRect r0 = rectOfWin();
        QList<QRect> seen;
        for (int cycle = 0; cycle < 3; ++cycle) {
            press(QStringLiteral("full"));
            QTest::qWait(500);
            const QRect full = rectOfWin();
            QVERIFY2(full.width() > r0.width() + 200, "full size did not grow the window");
            press(QStringLiteral("full"));
            QTest::qWait(500);
            seen << rectOfWin();
        }
        qInfo() << "content rect before" << r0 << "after 3 full-size cycles" << seen;
        // foot snaps its height to whole cells on the first commit after a resize, so the first cycle may differ from the
        // starting rect by that; what must not happen is drift: position and width stay, later cycles change nothing.
        QCOMPARE(seen.at(0).topLeft(), r0.topLeft());
        QCOMPARE(seen.at(0).width(), r0.width());
        QVERIFY(qAbs(seen.at(0).height() - r0.height()) <= 16);
        QCOMPARE(seen.at(1), seen.at(0));
        QCOMPARE(seen.at(2), seen.at(0));
        for (int cycle = 0; cycle < 3; ++cycle) {
            press(QStringLiteral("minimise"));
            QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("reason")).toString() == QLatin1String("minimised"); }));
            QProcess p; p.setProcessEnvironment(m_env);
            p.start(QStringLiteral(TAOS_WINDOW_BIN), {QStringLiteral("restore"), QString::number(id)});
            QVERIFY(p.waitForFinished(8000));
            QCOMPARE(p.exitCode(), 0);
            QTest::qWait(600);
            seen << rectOfWin();
        }
        qInfo() << "after 3 minimise/restore cycles" << seen.mid(3);
        for (int i = 3; i < seen.size(); ++i)
            QCOMPARE(seen.at(i), seen.at(0));
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }));
    }

    void closeActsOnTheRightWindow()
    {
        const int a = idOf(QStringLiteral("Web app")), b = idOf(QStringLiteral("Second app"));
        QVERIFY(a && b);
        QProcess p;
        p.setProcessEnvironment(m_env);
        p.start(QStringLiteral(TAOS_FRAME_BIN), {QStringLiteral("press"), QString::number(b), QStringLiteral("close")});
        QVERIFY(p.waitForFinished(5000));
        QCOMPARE(p.exitCode(), 0);
        QVERIFY(waitFor([&] { return idOf(QStringLiteral("Second app")) == 0; }));
        QVERIFY(waitFor([&] { return strip(b).isEmpty(); }));
        QCOMPARE(idOf(QStringLiteral("Web app")), a);                                   // untouched
        QVERIFY(!strip(a).isEmpty());
    }

    void followsTheButtonSideSetting()
    {
        const int id = idOf(QStringLiteral("Web app"));
        const QJsonObject deco = swayWindow(QStringLiteral("Web app")).value(QLatin1String("deco_rect")).toObject();
        writeFile(m_cfgDir + QStringLiteral("/taos/appearance.json"), "{\"theme\":\"graphite\",\"mode\":\"light\",\"windowButtons\":\"right\"}");
        QVERIFY2(waitFor([&] { return strip(id).value(QLatin1String("right")).toBool(); }, 6000), "the strip did not follow windowButtons");
        const int right = deco.value(QLatin1String("x")).toInt() + deco.value(QLatin1String("width")).toInt();
        QCOMPARE(strip(id).value(QLatin1String("x")).toInt(), right - 8 - 80);
        writeFile(m_cfgDir + QStringLiteral("/taos/appearance.json"), "{\"theme\":\"graphite\",\"mode\":\"dark\",\"windowButtons\":\"left\"}");
        QVERIFY(waitFor([&] { return !strip(id).value(QLatin1String("right")).toBool(); }, 6000));
    }

    void followsTheRulesFile()
    {
        const int id = idOf(QStringLiteral("Web app"));
        writeFile(m_cfgDir + QStringLiteral("/taos/frame.json"), "{\"exclude\":[\"^chrome-test\"]}");
        QVERIFY2(waitFor([&] { return strip(id).isEmpty(); }, 6000), "excluded window still has a strip");
        QVERIFY(waitFor([&] { return swayWindow(QStringLiteral("Web app")).value(QLatin1String("border")).toString() == QLatin1String("pixel"); }));
        writeFile(m_cfgDir + QStringLiteral("/taos/frame.json"), "{\"include\":[\"^chrome-\"]}");
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }, 6000));
        QCOMPARE(swayWindow(QStringLiteral("Web app")).value(QLatin1String("border")).toString(), QStringLiteral("normal"));
        // emptying the self decorating list frames the header bar app too
        writeFile(m_cfgDir + QStringLiteral("/taos/frame.json"), "{\"selfDecorating\":[]}");
        const int term = idOf(QStringLiteral("Plain terminal"));
        QVERIFY(waitFor([&] { return strip(term).value(QLatin1String("show")).toBool(); }, 6000));
        writeFile(m_cfgDir + QStringLiteral("/taos/frame.json"), "{}");
    }

    // A skin switch changes the gaps and bar of the workspace; sway then re-scales every floating window's position,
    // which walked windows off the screen over repeated switches. A floating window ends where it started.
    void aFloatingWindowKeepsItsPlaceAcrossSkinSwitches()
    {
        swaymsg({QStringLiteral("[title=\"Web app\"] floating enable, move absolute position 300 200")});
        QTest::qWait(1500);
        auto place = [&] { const QJsonObject r = swayWindow(QStringLiteral("Web app")).value(QLatin1String("rect")).toObject();
                           return QPoint(r.value(QLatin1String("x")).toInt(), r.value(QLatin1String("y")).toInt()); };
        const QPoint start = place();
        QVERIFY(!start.isNull());
        for (int i = 0; i < 3; ++i) {
            writeFile(m_cfgDir + QStringLiteral("/taos/appearance.json"), "{\"theme\":\"workbench\",\"mode\":\"dark\"}");
            QTest::qWait(3500);
            writeFile(m_cfgDir + QStringLiteral("/taos/appearance.json"), "{\"theme\":\"graphite\",\"mode\":\"dark\"}");
            QTest::qWait(3500);
        }
        const QPoint end = place();
        QVERIFY2((end - start).manhattanLength() <= 2, qPrintable(QStringLiteral("window moved from %1,%2 to %3,%4").arg(start.x()).arg(start.y()).arg(end.x()).arg(end.y())));
        swaymsg({QStringLiteral("[title=\"Web app\"] floating disable")});
    }

    void survivesASwayReload()
    {
        const int id = idOf(QStringLiteral("Web app"));
        swaymsg({QStringLiteral("reload")});
        // sway's config puts the snippet's font back; the bar goes back to 32 px and the strip stays
        QVERIFY(waitFor([&] { return swayWindow(QStringLiteral("Web app")).value(QLatin1String("deco_rect")).toObject().value(QLatin1String("height")).toInt() == 32; }, 12000));
        QVERIFY(waitFor([&] { return strip(id).value(QLatin1String("show")).toBool(); }));
        QVERIFY(m_frame.state() == QProcess::Running);
    }
};

QTEST_MAIN(FrameSway)
#include "test_frame_sway.moc"
