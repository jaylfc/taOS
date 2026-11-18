#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalServer>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>
#include <cstring>

#include "greetdclient.h"

static QByteArray frameOf(const QByteArray &payload) {
    quint32 len = quint32(payload.size());
    QByteArray f(4, 0);
    std::memcpy(f.data(), &len, 4);
    return f + payload;
}
static QByteArray jf(const QJsonObject &o) { return frameOf(QJsonDocument(o).toJson(QJsonDocument::Compact)); }
static QJsonObject authMsg(const char *kind, const char *text) {
    return {{"type", "auth_message"}, {"auth_message_type", kind}, {"auth_message", text}};
}
static const QJsonObject kOk{{"type", "success"}};
static QJsonObject authErr() { return {{"type", "error"}, {"error_type", "auth_error"}, {"description", "bad"}}; }

// A greetd stand-in that follows a script: for each request received it checks
// the request type and sends the scripted raw reply (empty = no reply).
struct Step {
    QString expect;
    QByteArray reply;
    bool closeAfter = false;
};
class Scripted : public QObject {
public:
    explicit Scripted(const QString &path) {
        QLocalServer::removeServer(path);
        server.listen(path);
        connect(&server, &QLocalServer::newConnection, this, [this] {
            sock = server.nextPendingConnection();
            connect(sock, &QLocalSocket::readyRead, this, [this] { onData(); });
        });
    }
    QLocalServer server;
    QLocalSocket *sock = nullptr;
    QList<Step> script;
    QList<QJsonObject> received;
    QStringList mismatches;
    QByteArray buf;
    void sendRaw(const QByteArray &b) { sock->write(b); sock->flush(); }

private:
    void onData() {
        buf += sock->readAll();
        while (buf.size() >= 4) {
            quint32 len;
            std::memcpy(&len, buf.constData(), 4);
            if (quint32(buf.size()) < 4 + len) return;
            const QJsonObject o = QJsonDocument::fromJson(buf.mid(4, int(len))).object();
            buf.remove(0, int(4 + len));
            received << o;
            if (script.isEmpty()) { mismatches << "unexpected " + o["type"].toString(); continue; }
            const Step st = script.takeFirst();
            if (o["type"].toString() != st.expect) mismatches << QString("expected %1 got %2").arg(st.expect, o["type"].toString());
            if (!st.reply.isEmpty()) sendRaw(st.reply);
            if (st.closeAfter) sock->disconnectFromServer();
        }
    }
};

class TestGreetdClient : public QObject {
    Q_OBJECT
    QTemporaryDir dir;
    QString path() const { return dir.path() + "/g.sock"; }
private slots:
    void successPath() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", jf(kOk)},
                      {"start_session", jf(kOk)}};
        GreetdClient c;
        c.setSessionCommand({"taos-desktop-session"});
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), started(&c, &GreetdClient::sessionStarting);
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        QCOMPARE(prompt.at(0).at(0).toString(), QString("secret"));
        QCOMPARE(prompt.at(0).at(1).toString(), QString("Password: "));
        c.respond("hunter2");
        QVERIFY(started.wait(2000));
        QVERIFY(fatal.isEmpty());
        QVERIFY2(srv.mismatches.isEmpty(), qPrintable(srv.mismatches.join(";")));
        QCOMPARE(srv.received.size(), 3);
        QCOMPARE(srv.received[0]["username"].toString(), QString("alex"));
        QCOMPARE(srv.received[1]["response"].toString(), QString("hunter2"));
        const QJsonArray cmd = srv.received[2]["cmd"].toArray();
        QCOMPARE(cmd.size(), 1);
        QCOMPARE(cmd[0].toString(), QString("taos-desktop-session"));
        QVERIFY(srv.received[2]["env"].isArray());
    }

    void wrongPasswordCancelsThenSucceeds() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", jf(authErr())},
                      {"cancel_session", jf(kOk)},
                      {"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", jf(kOk)},
                      {"start_session", jf(kOk)}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), failed(&c, &GreetdClient::authFailed),
            started(&c, &GreetdClient::sessionStarting);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.respond("wrong");
        QVERIFY(failed.wait(2000));
        QCOMPARE(failed.at(0).at(1).toBool(), true);  // credentials
        // authFailed arrives after greetd acknowledged the cancel: login is allowed now.
        QCOMPARE(srv.received.last()["type"].toString(), QString("cancel_session"));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.respond("right");
        QVERIFY(started.wait(2000));
        QVERIFY2(srv.mismatches.isEmpty(), qPrintable(srv.mismatches.join(";")));
        QStringList types;
        for (const auto &o : srv.received) types << o["type"].toString();
        QCOMPARE(types, QStringList({"create_session", "post_auth_message_response", "cancel_session", "create_session",
                                     "post_auth_message_response", "start_session"}));
    }

    void loginRightAfterAuthFailedSignalWorks() {
        // Re-login from inside the authFailed handler (what the controller does).
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", jf(authErr())},
                      {"cancel_session", jf(kOk)},
                      {"create_session", jf(authMsg("secret", "Password: "))}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        QSignalSpy prompt(&c, &GreetdClient::promptRequested);
        connect(&c, &GreetdClient::authFailed, &c, [&c] { c.login("alex"); });
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.respond("x");
        QTRY_COMPARE_WITH_TIMEOUT(prompt.size(), 2, 2000);
        QVERIFY2(srv.mismatches.isEmpty(), qPrintable(srv.mismatches.join(";")));
    }

    void multiPromptConversation() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", jf(authMsg("visible", "Verification code: "))},
                      {"post_auth_message_response", jf(authMsg("info", "Touch your key"))},
                      {"post_auth_message_response", jf(authMsg("error", "Key not recognised, retrying"))},
                      {"post_auth_message_response", jf(kOk)},
                      {"start_session", jf(kOk)}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), started(&c, &GreetdClient::sessionStarting);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.respond("pw");
        QTRY_COMPARE_WITH_TIMEOUT(prompt.size(), 2, 2000);
        QCOMPARE(prompt.at(1).at(0).toString(), QString("visible"));
        c.respond("123456");
        QVERIFY(started.wait(3000));
        QCOMPARE(prompt.size(), 4);
        QCOMPARE(prompt.at(2).at(0).toString(), QString("info"));
        QCOMPARE(prompt.at(3).at(0).toString(), QString("error"));
        QVERIFY2(srv.mismatches.isEmpty(), qPrintable(srv.mismatches.join(";")));
        // Messages are acknowledged with a null response.
        QVERIFY(srv.received[3]["response"].isNull());
        QVERIFY(srv.received[4]["response"].isNull());
    }

    void serverClosesMidConversation() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"post_auth_message_response", QByteArray(), true}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.respond("pw");
        QVERIFY(fatal.wait(2000));
        QVERIFY(fatal.at(0).at(0).toString().contains("closed"));
        QCOMPARE(fatal.size(), 1);
    }

    void oversizedFrameRejected() {
        Scripted srv(path());
        QByteArray huge(4, 0);
        quint32 len = 2u * 1024 * 1024;
        std::memcpy(huge.data(), &len, 4);
        srv.script = {{"create_session", huge + QByteArray(100, 'x')}};
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(fatal.wait(2000));
        QVERIFY(fatal.at(0).at(0).toString().contains("frame"));
    }

    void zeroLengthFrameRejected() {
        Scripted srv(path());
        srv.script = {{"create_session", QByteArray(4, 0)}};
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(fatal.wait(2000));
    }

    void malformedFramesRejected_data() {
        QTest::addColumn<QByteArray>("reply");
        QTest::newRow("not json") << frameOf("{{{ nope");
        QTest::newRow("json array") << frameOf("[1,2,3]");
        QTest::newRow("unknown type") << jf({{"type", "surprise"}});
        QTest::newRow("no type") << jf({{"x", 1}});
        QTest::newRow("bad auth type") << jf(authMsg("telepathy", "?"));
    }
    void malformedFramesRejected() {
        QFETCH(QByteArray, reply);
        Scripted srv(path());
        srv.script = {{"create_session", reply}};
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(fatal.wait(2000));
        QCOMPARE(fatal.size(), 1);
    }

    void truncatedFrameThenCloseIsFatal() {
        Scripted srv(path());
        QByteArray half = jf(kOk);
        half.chop(3);
        srv.script = {{"create_session", half, true}};
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(fatal.wait(2000));
    }

    void replyTimeoutIsFatal() {
        // start_session is answered at once by a real greetd: silence is fatal.
        Scripted srv(path());
        srv.script = {{"create_session", jf(kOk)}, {"start_session", QByteArray()}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        c.setReplyTimeoutMs(150);
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(fatal.wait(2000));
        QVERIFY(fatal.at(0).at(0).toString().contains("in time"));
    }

    void slowPamIsNotFatal() {
        // PAM blocks on a fingerprint or touch key: create_session and the answer
        // to an info message may take longer than the reply timeout.
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("info", "Touch your key"))},
                      {"post_auth_message_response", QByteArray()}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        c.setReplyTimeoutMs(100);
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), fatal(&c, &GreetdClient::fatal),
            started(&c, &GreetdClient::sessionStarting);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        QTest::qWait(450);  // several timeouts later, greetd is still waiting on the key
        QVERIFY(fatal.isEmpty());
        srv.script.prepend({"start_session", jf(kOk)});
        srv.sendRaw(jf(kOk));  // the touch happened: authenticated
        QVERIFY(started.wait(2000));
        QVERIFY(fatal.isEmpty());
    }

    void waitingForTheUserIsNotTimed() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))}};
        GreetdClient c;
        c.setReplyTimeoutMs(100);
        QSignalSpy prompt(&c, &GreetdClient::promptRequested), fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        QTest::qWait(400);
        QVERIFY(fatal.isEmpty());
    }

    void unsolicitedReplyIsFatal() {
        Scripted srv(path());
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        QTRY_VERIFY(srv.sock);
        srv.sendRaw(jf(kOk));
        QVERIFY(fatal.wait(2000));
    }

    void cancelWhilePromptingSendsCancel() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))}, {"cancel_session", jf(kOk)}};
        GreetdClient c;
        QSignalSpy prompt(&c, &GreetdClient::promptRequested);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.cancel();
        QTRY_COMPARE(srv.received.size(), 2);
        QCOMPARE(srv.received[1]["type"].toString(), QString("cancel_session"));
        QTRY_COMPARE(c.state(), GreetdClient::State::Idle);
    }

    void switchingUserCancelsFirst() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(authMsg("secret", "Password: "))},
                      {"cancel_session", jf(kOk)},
                      {"create_session", jf(authMsg("secret", "Password: "))}};
        GreetdClient c;
        QSignalSpy prompt(&c, &GreetdClient::promptRequested);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(prompt.wait(2000));
        c.login("sam");
        QTRY_COMPARE_WITH_TIMEOUT(prompt.size(), 2, 2000);
        QCOMPARE(srv.received[2]["username"].toString(), QString("sam"));
        QVERIFY2(srv.mismatches.isEmpty(), qPrintable(srv.mismatches.join(";")));
    }

    void startSessionErrorReportsFailure() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(kOk)},
                      {"start_session", jf({{"type", "error"}, {"error_type", "error"}, {"description", "no such command"}})},
                      {"cancel_session", jf(kOk)}};
        GreetdClient c;
        c.setSessionCommand({"nope"});
        QSignalSpy failed(&c, &GreetdClient::authFailed), started(&c, &GreetdClient::sessionStarting);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(failed.wait(2000));
        QCOMPARE(failed.at(0).at(1).toBool(), false);
        QVERIFY(started.isEmpty());
    }

    void closingAfterStartSessionIsNotAnError() {
        Scripted srv(path());
        srv.script = {{"create_session", jf(kOk)}, {"start_session", jf(kOk), true}};
        GreetdClient c;
        c.setSessionCommand({"s"});
        QSignalSpy started(&c, &GreetdClient::sessionStarting), fatal(&c, &GreetdClient::fatal);
        QVERIFY(c.connectToGreetd(path()));
        c.login("alex");
        QVERIFY(started.wait(2000));
        QTest::qWait(200);
        QVERIFY(fatal.isEmpty());
    }

    void connectToMissingSocketIsFatal() {
        GreetdClient c;
        QSignalSpy fatal(&c, &GreetdClient::fatal);
        QVERIFY(!c.connectToGreetd(dir.path() + "/nothing-here.sock"));
        QCOMPARE(fatal.size(), 1);
    }
};
QTEST_MAIN(TestGreetdClient)
#include "test_greetdclient.moc"
