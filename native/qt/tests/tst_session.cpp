#include <QSignalSpy>
#include <QTemporaryFile>
#include <QtTest>

#include "fakecontroller.h"
#include "taosqt/api.h"
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

        s.signIn(u"jay"_s, u"wrong"_s);
        QTRY_COMPARE(failed.size(), 1);
        QCOMPARE(qvariant_cast<Error>(failed.first().first()).userMessage(), u"Invalid username or password"_s);
        QCOMPARE(s.errorText(), u"Invalid username or password"_s);
        QCOMPARE(need.size(), 1); // a wrong password is not a new prompt

        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QCOMPARE(s.userName(), u"jay"_s);
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
            s.signIn(u"jay"_s, FAKE_PASSWORD);
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

    void savedSessionRoundTrip()
    {
        QByteArray stored;
        {
            Session a(fastOptions(fake.url()));
            auto *src = new SavedSessionTokenSource(QByteArray());
            connect(src, &SavedSessionTokenSource::sessionToStore, this, [&](const QByteArray &j) { stored = j; });
            a.setTokenSource(src);
            a.signIn(u"jay"_s, FAKE_PASSWORD, true);
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
        s.signIn(u"jay"_s, FAKE_PASSWORD);
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

        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        QTRY_COMPARE(stream->state(), EventStream::Live);
    }

    void signOutForgetsEverything()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
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
