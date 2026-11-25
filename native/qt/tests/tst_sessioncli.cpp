// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-session, the command line face of the desktop's shared sign-in, run as a real process against the
// fake controller: login writes the owner-only session file, status reads it, a second login-less app can use
// it, logout revokes it at the controller and removes it, and a refused or missing session is "signed out".
#include <QFileInfo>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>
#include <memory>

#include <unistd.h>

#include "fakecontroller.h"
#include "taosqt/desktopsession.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;

class TstSessionCli : public QObject
{
    Q_OBJECT
    FakeController fake;

    struct Run { int code; QString out; };
    QString m_sock;
    // the fake desktop sign-in socket; `unlinked` answers every request with a refusal
    QProcess *startSso(const QString &sock, bool unlinked = false)
    {
        auto *p = new QProcess;
        p->setProgram(u"python3"_s);
        QStringList a{QStringLiteral(FAKE_SSO_PY), sock, fake.url().toString()};
        if (unlinked)
            a << u"unlinked"_s;
        p->setArguments(a);
        p->start();
        if (!p->waitForReadyRead(10000) || !p->readLine().startsWith("READY")) { delete p; return nullptr; }
        return p;
    }
    Run run(const QString &runtimeDir, const QStringList &args, const QByteArray &stdinText = {}, const QUrl &url = {})
    {
        QProcess p;
        QProcessEnvironment env;
        if (!m_sock.isEmpty())
            env.insert(u"TAOS_DESKTOP_SSO_SOCKET"_s, m_sock);
        env.insert(u"XDG_RUNTIME_DIR"_s, runtimeDir);
        env.insert(u"TAOS_URL"_s, (url.isEmpty() ? fake.url() : url).toString());
        env.insert(u"QT_QPA_PLATFORM"_s, u"offscreen"_s);
        p.setProcessEnvironment(env);
        p.start(QStringLiteral(TAOS_SESSION_BIN), args);
        if (!stdinText.isEmpty())
            p.write(stdinText);
        p.closeWriteChannel();
        if (!p.waitForFinished(30000)) {
            p.kill();
            return {-1, u"timeout"_s};
        }
        return {p.exitCode(), QString::fromUtf8(p.readAllStandardOutput()) + QString::fromUtf8(p.readAllStandardError())};
    }

private Q_SLOTS:
    void initTestCase() { QVERIFY2(fake.ok(), "python3 fake controller did not start"); }
    void init() { fake.reset(); }

    void signedOutWithNothingStored()
    {
        QTemporaryDir rt;
        const Run r = run(rt.path(), {u"status"_s});
        QCOMPARE(r.code, 1);
        QVERIFY2(r.out.contains(u"signed out"_s), qPrintable(r.out));
    }

    void loginWritesAnOwnerOnlySessionThatStatusAndLogoutUse()
    {
        QTemporaryDir rt;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        const QString file = rt.filePath(u"taos/session"_s);

        // wrong password: refused, nothing stored
        Run r = run(rt.path(), {u"login"_s}, "wrong-password\n");
        QCOMPARE(r.code, 1);
        QVERIFY(!QFileInfo::exists(file));

        // the password is read from stdin only
        r = run(rt.path(), {u"login"_s, u"--user"_s, u"alex"_s}, FAKE_PASSWORD.toUtf8() + "\n");
        QCOMPARE(r.code, 0);
        QVERIFY2(r.out.contains(u"signed in as"_s), qPrintable(r.out));
        QVERIFY(!r.out.contains(FAKE_PASSWORD));
        QVERIFY(QFileInfo::exists(file));
        const auto others = QFileDevice::ReadGroup | QFileDevice::WriteGroup | QFileDevice::ExeGroup
            | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther;
        QVERIFY(!(QFileInfo(file).permissions() & others));
        QVERIFY(!(QFileInfo(rt.filePath(u"taos"_s)).permissions() & others));
        QCOMPARE(QFileInfo(file).ownerId(), ::getuid());

        // status uses the stored session, without printing it
        r = run(rt.path(), {u"status"_s});
        QCOMPARE(r.code, 0);
        QVERIFY2(r.out.contains(u"signed in as"_s), qPrintable(r.out));
        QFile f(file);
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QByteArray saved = f.readAll().trimmed();
        f.close();
        QVERIFY(!saved.isEmpty());
        QVERIFY(!r.out.contains(QString::fromUtf8(saved)));

        // logout: the controller forgets the session AND the file is gone
        r = run(rt.path(), {u"logout"_s});
        QCOMPARE(r.code, 0);
        QVERIFY(!QFileInfo::exists(file));
        Session replay(fastOptions(fake.url()));
        replay.setTokenSource(new SavedSessionTokenSource(saved));
        QSignalSpy need(&replay, &Session::signInRequired);
        replay.start();
        QTRY_COMPARE(need.size(), 1); // the old cookie is refused at the controller
        QCOMPARE(run(rt.path(), {u"status"_s}).code, 1);
    }

    void aRefusedSessionIsDroppedAndReportedSignedOut()
    {
        QTemporaryDir rt;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        QCOMPARE(run(rt.path(), {u"login"_s}, FAKE_PASSWORD.toUtf8() + "\n").code, 0);
        fake.config({{u"expire"_s, true}});
        const Run r = run(rt.path(), {u"status"_s});
        QCOMPARE(r.code, 1);
        QVERIFY(!QFileInfo::exists(rt.filePath(u"taos/session"_s)));
    }

    void desktopSignInNeedsNoPasswordAndUsesTheKernelsAnswer()
    {
        QTemporaryDir rt, sd;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        m_sock = sd.filePath(u"s.sock"_s);
        std::unique_ptr<QProcess> sso(startSso(m_sock));
        QVERIFY(sso);
        const QString file = rt.filePath(u"taos/session"_s);
        Run r = run(rt.path(), {u"acquire"_s});
        QCOMPARE(r.code, 0);
        QVERIFY2(r.out.contains(u"desktop sign-in"_s), qPrintable(r.out));
        const auto others = QFileDevice::ReadGroup | QFileDevice::WriteGroup | QFileDevice::ExeGroup
            | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther;
        QVERIFY(QFileInfo::exists(file));
        QVERIFY(!(QFileInfo(file).permissions() & others));
        // the controller accepts it: status verifies it with /auth/me
        r = run(rt.path(), {u"status"_s});
        QCOMPARE(r.code, 0);
        QVERIFY2(r.out.contains(u"signed in as"_s), qPrintable(r.out));
        // a password sign-in already stored is never overwritten
        QCOMPARE(run(rt.path(), {u"login"_s}, FAKE_PASSWORD.toUtf8() + "\n").code, 0);
        QCOMPARE(run(rt.path(), {u"acquire"_s}).code, 1);
        m_sock.clear();
    }

    void anUnlinkedAccountOrNoSocketLeavesTheSharedSignInAsTheFallback()
    {
        QTemporaryDir rt, sd;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        m_sock = sd.filePath(u"s.sock"_s);
        QCOMPARE(run(rt.path(), {u"acquire"_s}).code, 1); // no socket
        std::unique_ptr<QProcess> sso(startSso(m_sock, true));
        QVERIFY(sso);
        const Run r = run(rt.path(), {u"acquire"_s});
        QCOMPARE(r.code, 1);
        QVERIFY2(r.out.contains(u"not linked"_s), qPrintable(r.out));
        QVERIFY(!QFileInfo::exists(rt.filePath(u"taos/session"_s)));
        // the service has nothing to do and exits cleanly (the supervisor does not restart a clean exit)
        const Run d = run(rt.path(), {u"sso"_s});
        QCOMPARE(d.code, 0);
        m_sock.clear();
    }

    void theServiceSignsInAtLoginAndKeepsASignOut()
    {
        QTemporaryDir rt, sd;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        m_sock = sd.filePath(u"s.sock"_s);
        std::unique_ptr<QProcess> fakeSso(startSso(m_sock));
        QVERIFY(fakeSso);
        const QString file = rt.filePath(u"taos/session"_s);
        QProcess svc;
        QProcessEnvironment env;
        env.insert(u"XDG_RUNTIME_DIR"_s, rt.path());
        env.insert(u"TAOS_URL"_s, fake.url().toString());
        env.insert(u"TAOS_DESKTOP_SSO_SOCKET"_s, m_sock);
        env.insert(u"QT_QPA_PLATFORM"_s, u"offscreen"_s);
        svc.setProcessEnvironment(env);
        svc.start(QStringLiteral(TAOS_SESSION_BIN), {u"sso"_s});
        QVERIFY(svc.waitForStarted());
        QTRY_VERIFY_WITH_TIMEOUT(QFileInfo::exists(file), 10000); // signed in at login, no password
        QCOMPARE(run(rt.path(), {u"status"_s}).code, 0);
        // a sign-out stays a sign-out: the service does not sign the person back in
        QCOMPARE(run(rt.path(), {u"logout"_s}).code, 0);
        QVERIFY(!QFileInfo::exists(file));
        QTest::qWait(6500);
        QVERIFY(!QFileInfo::exists(file));
        // asking for it by hand is a sign-in again; stopping the service is not a sign-out
        QCOMPARE(run(rt.path(), {u"acquire"_s}).code, 0);
        QVERIFY(QFileInfo::exists(file));
        svc.terminate();
        QVERIFY(svc.waitForFinished(10000));
        QCOMPARE(svc.exitCode(), 0);
        QVERIFY(QFileInfo::exists(file)); // restarting the shell pieces never signs anyone out
        m_sock.clear();
    }

    void anUnreachableControllerIsNotSignedOut()
    {
        QTemporaryDir rt;
        QVERIFY(QFile::setPermissions(rt.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        // a stored session names its own controller, so make that controller go away
        auto *gone = new FakeController;
        QVERIFY(gone->ok());
        QCOMPARE(run(rt.path(), {u"login"_s}, FAKE_PASSWORD.toUtf8() + "\n", gone->url()).code, 0);
        delete gone;
        const Run r = run(rt.path(), {u"status"_s});
        QCOMPARE(r.code, 2);
        QVERIFY2(r.out.contains(u"unreachable"_s), qPrintable(r.out));
        // the stored session is kept: the controller may simply be restarting
        QVERIFY(QFileInfo::exists(rt.filePath(u"taos/session"_s)));
    }
};

QTEST_MAIN(TstSessionCli)
#include "tst_sessioncli.moc"
