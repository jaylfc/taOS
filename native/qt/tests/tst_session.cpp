#include <QSignalSpy>
#include <QFileInfo>
#include <QTemporaryDir>
#include <QTemporaryFile>
#include <QtTest>

#include "fakecontroller.h"
#include "taosqt/api.h"
#include "taosqt/desktopsession.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;

class TstSession : public QObject
{
    Q_OBJECT
    FakeController fake;

private Q_SLOTS:
    void initTestCase() { QVERIFY2(fake.ok(), "python3 fake controller did not start"); }
    void init()
    {
        fake.reset();
        fake.config({{u"heartbeat_ms"_s, 40}});
    }

    void passwordSignInFlow()
    {
        Session s(fastOptions(fake.url()));
        QSignalSpy need(&s, &Session::signInRequired);
        QSignalSpy failed(&s, &Session::signInFailed);
        QSignalSpy in(&s, &Session::signedIn);
        QCOMPARE(s.state(), Session::SignedOut);

        s.start();
        QCOMPARE(need.size(), 1); // the password source asks the user at once
        QCOMPARE(s.state(), Session::SignedOut);

        s.signIn(u"alex"_s, u"wrong"_s);
        QTRY_COMPARE(failed.size(), 1);
        QCOMPARE(qvariant_cast<Error>(failed.first().first()).userMessage(), u"Invalid username or password"_s);
        QCOMPARE(s.errorText(), u"Invalid username or password"_s);
        QCOMPARE(need.size(), 1); // a wrong password is not a new prompt

        s.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QCOMPARE(s.userName(), u"alex"_s);
        QCOMPARE(in.size(), 1);
        QVERIFY(s.errorText().isEmpty());
    }

    void localTokenNeedsNoPromptAndFallsBackWhenRefused()
    {
        {
            Session s(fastOptions(fake.url()));
            s.setTokenSource(new LocalTokenSource(u"local-token-123"_s));
            QSignalSpy need(&s, &Session::signInRequired);
            s.start();
            QTRY_COMPARE(s.state(), Session::SignedIn);
            QCOMPARE(need.size(), 0);
        }
        {
            Session s(fastOptions(fake.url()));
            s.setTokenSource(new LocalTokenSource(u"not-the-token"_s));
            QSignalSpy need(&s, &Session::signInRequired);
            s.start();
            QTRY_COMPARE(need.size(), 1);
            QCOMPARE(s.state(), Session::SignedOut);
            // The user then signs in with a password and it works.
            s.signIn(u"alex"_s, FAKE_PASSWORD);
            QTRY_COMPARE(s.state(), Session::SignedIn);
        }
        {
            QTemporaryFile f;
            QVERIFY(f.open());
            f.write("local-token-123\n");
            f.flush();
            Session s(fastOptions(fake.url()));
            s.setTokenSource(LocalTokenSource::fromFile(f.fileName()));
            s.start();
            QTRY_COMPARE(s.state(), Session::SignedIn);
        }
    }

    void aBearerIsRefusedWhileTheControllerIgnoresItOnAuthMe()
    {
        // Reproduces the controller as of 2026-11: /auth/me reads only the cookie.
        fake.config({{u"me_cookie_only"_s, true}});
        Session s(fastOptions(fake.url()));
        s.setTokenSource(new LocalTokenSource(u"local-token-123"_s));
        QSignalSpy need(&s, &Session::signInRequired);
        s.start();
        QTRY_COMPARE(need.size(), 1);
        QCOMPARE(s.state(), Session::SignedOut);
    }

    void desktopSessionIsSharedBetweenApps()
    {
        QTemporaryDir run;
        QVERIFY(run.isValid());
        const QString file = run.filePath(u"taos/session"_s);

        Session a(fastOptions(fake.url()));
        a.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy needA(&a, &Session::signInRequired);
        a.start();
        QCOMPARE(needA.size(), 1); // nothing stored yet: ask once
        a.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE(a.state(), Session::SignedIn);

        // Owner-only: directory 0700, file 0600.
        QVERIFY(QFileInfo::exists(file));
        const auto others = QFileDevice::ReadGroup | QFileDevice::WriteGroup | QFileDevice::ExeGroup
            | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther;
        const auto fp = QFileInfo(file).permissions();
        QVERIFY((fp & QFileDevice::ReadOwner) && (fp & QFileDevice::WriteOwner) && !(fp & QFileDevice::ExeOwner));
        QVERIFY(!(fp & others));
        const auto dp = QFileInfo(run.filePath(u"taos"_s)).permissions();
        QVERIFY((dp & QFileDevice::ReadOwner) && (dp & QFileDevice::WriteOwner) && (dp & QFileDevice::ExeOwner));
        QVERIFY(!(dp & others));

        // A second app starts later: signed in without a prompt.
        Session b(fastOptions(fake.url()));
        b.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy needB(&b, &Session::signInRequired);
        b.start();
        QTRY_COMPARE(b.state(), Session::SignedIn);
        QCOMPARE(needB.size(), 0);

        // A file others can read is never trusted.
        QFile::setPermissions(file, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ReadGroup);
        Session c(fastOptions(fake.url()));
        c.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy needC(&c, &Session::signInRequired);
        c.start();
        QCOMPARE(needC.size(), 1);
        QFile::setPermissions(file, QFileDevice::ReadOwner | QFileDevice::WriteOwner);

        // Signing out in one app removes the file.
        a.signOut();
        QVERIFY(!QFileInfo::exists(file));
    }

    void anAppWaitingForSignInPicksUpAnotherAppsSignIn()
    {
        QTemporaryDir run;
        QVERIFY(run.isValid());
        Session waiting(fastOptions(fake.url()));
        waiting.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy need(&waiting, &Session::signInRequired);
        waiting.start();
        QCOMPARE(need.size(), 1);

        Session other(fastOptions(fake.url()));
        other.setTokenSource(new DesktopSessionTokenSource(run.path()));
        other.start();
        other.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE_WITH_TIMEOUT(waiting.state(), Session::SignedIn, 5000);
        QCOMPARE(need.size(), 1); // no second prompt
    }

    void aStaleDesktopSessionIsDroppedAndTheUserIsAskedOnce()
    {
        QTemporaryDir run;
        QVERIFY(run.isValid());
        {
            Session a(fastOptions(fake.url()));
            a.setTokenSource(new DesktopSessionTokenSource(run.path()));
            a.signIn(u"alex"_s, FAKE_PASSWORD);
            QTRY_COMPARE(a.state(), Session::SignedIn);
        }
        const QString file = run.filePath(u"taos/session"_s);
        QVERIFY(QFileInfo::exists(file));
        fake.config({{u"expire"_s, true}}); // the controller forgot every session
        Session b(fastOptions(fake.url()));
        b.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy need(&b, &Session::signInRequired);
        b.start();
        QTRY_COMPARE(need.size(), 1);
        QVERIFY(!QFileInfo::exists(file));
    }

    void savedSessionRoundTrip()
    {
        QByteArray stored;
        {
            Session a(fastOptions(fake.url()));
            auto *src = new SavedSessionTokenSource(QByteArray());
            connect(src, &SavedSessionTokenSource::sessionToStore, this, [&](const QByteArray &j) { stored = j; });
            a.setTokenSource(src);
            a.signIn(u"alex"_s, FAKE_PASSWORD, true);
            QTRY_COMPARE(a.state(), Session::SignedIn);
            QVERIFY(!stored.isEmpty());
        }
        Session b(fastOptions(fake.url()));
        b.setTokenSource(new SavedSessionTokenSource(stored));
        QSignalSpy need(&b, &Session::signInRequired);
        b.start();
        QTRY_COMPARE(b.state(), Session::SignedIn);
        QCOMPARE(need.size(), 0);

        // An expired one asks the user instead of looping.
        fake.config({{u"expire"_s, true}});
        Session c(fastOptions(fake.url()));
        c.setTokenSource(new SavedSessionTokenSource(stored));
        QSignalSpy need2(&c, &Session::signInRequired);
        c.start();
        QTRY_COMPARE(need2.size(), 1);
        QCOMPARE(c.state(), Session::SignedOut);
    }

    void theTokenSourceCanBeReplacedAtRuntime()
    {
        Session s(fastOptions(fake.url()));
        QSignalSpy need(&s, &Session::signInRequired);
        s.start();
        QCOMPARE(need.size(), 1);
        QCOMPARE(s.tokenSource()->name(), u"password"_s);
        s.setTokenSource(new LocalTokenSource(u"local-token-123"_s)); // the old one is deleted
        QCOMPARE(s.tokenSource()->name(), u"local-token"_s);
        s.start();
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QCOMPARE(need.size(), 1);
    }

    void aSourceThatOffersARejectedCredentialAgainDoesNotLoop()
    {
        struct Stubborn : TokenSource {
            int calls = 0;
            QString name() const override { return u"stubborn"_s; }
            void acquire(Session *s, const Error &) override
            {
                ++calls;
                s->useBearerToken(u"always-wrong"_s);
            }
        };
        Session s(fastOptions(fake.url()));
        auto *src = new Stubborn;
        s.setTokenSource(src);
        QSignalSpy need(&s, &Session::signInRequired);
        s.start();
        QTRY_COMPARE(need.size(), 1);
        QVERIFY(src->calls <= 4);
        QCOMPARE(s.state(), Session::SignedOut);
    }

    void expiryPromptsOnceAndStreamsResume()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QSignalSpy need(&s, &Session::signInRequired);
        QSignalSpy out(&s, &Session::stateChanged);
        auto *stream = s.client()->osEvents(); // the stream and the calls fail together
        stream->start();
        QTRY_COMPARE(stream->state(), EventStream::Live);

        fake.config({{u"expire"_s, true}, {u"drop_sse"_s, true}});
        for (int i = 0; i < 4; ++i)
            Notifications(s.client()).list();
        QTRY_COMPARE(need.size(), 1);
        QTRY_COMPARE(stream->state(), EventStream::AuthRequired);
        QTest::qWait(200);
        QCOMPARE(need.size(), 1); // still one
        QCOMPARE(s.state(), Session::SignedOut);

        s.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QTRY_COMPARE(stream->state(), EventStream::Live);
    }

    void signOutForgetsEverything()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"alex"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QSignalSpy outSpy(&s, &Session::signedOut);
        s.signOut();
        QCOMPARE(outSpy.size(), 1);
        QCOMPARE(s.state(), Session::SignedOut);
        QTRY_VERIFY(!s.client()->hasCredentials());
        QVERIFY(s.userName().isEmpty());
    }

    void unreachableControllerIsAFailureNotAPrompt()
    {
        Session s(fastOptions(QUrl(u"http://127.0.0.1:9"_s)));
        s.setTokenSource(new LocalTokenSource(u"local-token-123"_s));
        QSignalSpy failed(&s, &Session::signInFailed);
        QSignalSpy need(&s, &Session::signInRequired);
        s.start();
        QTRY_COMPARE(failed.size(), 1);
        QVERIFY(qvariant_cast<Error>(failed.first().first()).isRetryable());
        QCOMPARE(need.size(), 0);
    }
};

QTEST_MAIN(TstSession)
#include "tst_session.moc"
