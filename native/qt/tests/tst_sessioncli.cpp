// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-session, the command line face of the desktop's shared sign-in, run as a real process against the
// fake controller: login writes the owner-only session file, status reads it, a second login-less app can use
// it, logout revokes it at the controller and removes it, and a refused or missing session is "signed out".
#include <QFileInfo>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>

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
    Run run(const QString &runtimeDir, const QStringList &args, const QByteArray &stdinText = {}, const QUrl &url = {})
    {
        QProcess p;
        QProcessEnvironment env;
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
