// Integration: the fallback engine against a real headless sway with two real windows (foot).
// Skipped cleanly when there is no sway or foot binary.
#include <QDir>
#include <QElapsedTimer>
#include <QProcess>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>
#include <functional>

#include "swayengine.h"

class SwayIntegration : public QObject {
    Q_OBJECT
    QTemporaryDir m_dir;
    QProcess m_sway;
    QVector<QProcess *> m_clients;
    QString m_sock, m_display;
    SwayEngine *m_eng = nullptr;

    static WindowInfo byApp(SwayEngine *e, const QString &app) {
        for (const WindowInfo &w : e->windows()) if (w.appId == app) return w;
        return WindowInfo();
    }
    bool waitFor(SwayEngine *e, std::function<bool()> pred, int ms = 8000) {
        QElapsedTimer t; t.start();
        while (t.elapsed() < ms) {
            if (pred()) return true;
            e->refresh();
            QTest::qWait(120);
        }
        return pred();
    }
private slots:
    void initTestCase() {
        if (QStandardPaths::findExecutable(QStringLiteral("sway")).isEmpty() || QStandardPaths::findExecutable(QStringLiteral("foot")).isEmpty())
            QSKIP("no sway or foot binary");
        QVERIFY(m_dir.isValid());
        const QString run = m_dir.path() + QStringLiteral("/run");
        QDir().mkpath(run);
        QFile::setPermissions(run, QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
        QFile cfg(m_dir.path() + QStringLiteral("/config"));
        QVERIFY(cfg.open(QIODevice::WriteOnly));
        cfg.write("default_border none\n");
        cfg.close();
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert(QStringLiteral("XDG_RUNTIME_DIR"), run);
        env.insert(QStringLiteral("WLR_BACKENDS"), QStringLiteral("headless"));
        env.insert(QStringLiteral("WLR_LIBINPUT_NO_DEVICES"), QStringLiteral("1"));
        env.insert(QStringLiteral("WLR_RENDERER"), QStringLiteral("pixman"));
        env.remove(QStringLiteral("WAYLAND_DISPLAY"));
        env.remove(QStringLiteral("SWAYSOCK"));
        m_sway.setProcessEnvironment(env);
        m_sway.start(QStringLiteral("sway"), {QStringLiteral("-c"), m_dir.path() + QStringLiteral("/config")});
        QVERIFY(m_sway.waitForStarted());
        QElapsedTimer t; t.start();
        while (t.elapsed() < 10000 && (m_sock.isEmpty() || m_display.isEmpty())) {
            for (const QString &f : QDir(run).entryList(QDir::System | QDir::Files | QDir::NoDotAndDotDot)) {
                if (f.startsWith(QLatin1String("sway-ipc"))) m_sock = run + QLatin1Char('/') + f;
                if (f.startsWith(QLatin1String("wayland-")) && !f.endsWith(QLatin1String(".lock"))) m_display = f;
            }
            QTest::qWait(100);
        }
        if (m_sock.isEmpty() || m_display.isEmpty())
            QSKIP(qPrintable(QStringLiteral("sway did not start headless: ") + QString::fromUtf8(m_sway.readAllStandardError().left(300))));
        for (const char *app : {"dockA", "dockB"}) {
            auto *c = new QProcess(this);
            QProcessEnvironment ce = env;
            ce.insert(QStringLiteral("WAYLAND_DISPLAY"), m_display);
            c->setProcessEnvironment(ce);
            c->start(QStringLiteral("foot"), {QStringLiteral("-a"), QString::fromLatin1(app), QStringLiteral("-T"),
                                              QStringLiteral("title-") + QString::fromLatin1(app), QStringLiteral("sleep"), QStringLiteral("300")});
            m_clients << c;
        }
        m_eng = new SwayEngine(m_sock, this);
        m_eng->start();
        QVERIFY2(waitFor(m_eng, [&] { return m_eng->windows().size() == 2; }), "two windows did not appear");
    }
    void cleanupTestCase() {
        for (QProcess *c : std::as_const(m_clients)) { c->kill(); c->waitForFinished(1000); }
        m_sway.terminate();
        if (!m_sway.waitForFinished(3000)) { m_sway.kill(); m_sway.waitForFinished(1000); }
    }
    void listsWindowsWithAppIdAndTitle() {
        const WindowInfo a = byApp(m_eng, QStringLiteral("dockA"));
        QVERIFY(a.id > 0);
        QCOMPARE(a.title, QStringLiteral("title-dockA"));
        QVERIFY(a.pid > 0);
        QVERIFY(!a.minimised);
    }
    void focusMovesFocus() {
        const int a = byApp(m_eng, QStringLiteral("dockA")).id, b = byApp(m_eng, QStringLiteral("dockB")).id;
        m_eng->focus(a);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockA")).focused; }));
        m_eng->focus(b);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockB")).focused; }));
        QVERIFY(!byApp(m_eng, QStringLiteral("dockA")).focused);
    }
    void minimiseAndRestore() {
        const int a = byApp(m_eng, QStringLiteral("dockA")).id;
        m_eng->minimise(a);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockA")).minimised; }));
        QVERIFY(!byApp(m_eng, QStringLiteral("dockB")).minimised);
        QCOMPARE(m_eng->windows().size(), 2);   // minimised windows stay in the list
        m_eng->restore(a);
        QVERIFY(waitFor(m_eng, [&] { const WindowInfo w = byApp(m_eng, QStringLiteral("dockA")); return w.id && !w.minimised && w.focused; }));
    }
    void toggleMinimise() {
        const int a = byApp(m_eng, QStringLiteral("dockA")).id;
        m_eng->toggleMinimise(a);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockA")).minimised; }));
        m_eng->toggleMinimise(a);
        QVERIFY(waitFor(m_eng, [&] { const WindowInfo w = byApp(m_eng, QStringLiteral("dockA")); return w.id && !w.minimised; }));
    }
    void focusRestoresMinimised() {
        const int a = byApp(m_eng, QStringLiteral("dockA")).id;
        m_eng->minimise(a);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockA")).minimised; }));
        m_eng->focus(a);
        QVERIFY(waitFor(m_eng, [&] { const WindowInfo w = byApp(m_eng, QStringLiteral("dockA")); return w.id && !w.minimised && w.focused; }));
    }
    void restartRebuildsMinimisedFromCompositor() {
        const int b = byApp(m_eng, QStringLiteral("dockB")).id;
        m_eng->minimise(b);
        QVERIFY(waitFor(m_eng, [&] { return byApp(m_eng, QStringLiteral("dockB")).minimised; }));
        SwayEngine fresh(m_sock);   // a restarted dock: no memory at all
        fresh.start();
        QVERIFY(waitFor(&fresh, [&] { return fresh.windows().size() == 2; }));
        QVERIFY(byApp(&fresh, QStringLiteral("dockB")).minimised);
        QVERIFY(!byApp(&fresh, QStringLiteral("dockA")).minimised);
        fresh.restore(b);
        QVERIFY(waitFor(&fresh, [&] { const WindowInfo w = byApp(&fresh, QStringLiteral("dockB")); return w.id && !w.minimised; }));
    }
    void closeRemovesWindow() {
        const int b = byApp(m_eng, QStringLiteral("dockB")).id;
        m_eng->close(b);
        QVERIFY(waitFor(m_eng, [&] { return m_eng->windows().size() == 1; }));
    }
};
QTEST_MAIN(SwayIntegration)
#include "test_sway_integration.moc"
