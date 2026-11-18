#pragma once
#include <QLocalServer>
#include <QLocalSocket>
#include <QObject>
#include <QPointer>
#include <QStringList>

// FakeGreetd: a stand-in for greetd for `taos-greeter --demo` and the tests.
// It speaks the real wire protocol on a real local socket, so the demo
// exercises GreetdClient exactly as production does.
//   password "demo" succeeds, anything else is an auth_error after 600 ms;
//   with otp enabled a second, visible prompt follows and wants "123456".
class FakeGreetd : public QObject {
    Q_OBJECT
public:
    explicit FakeGreetd(QObject *parent = nullptr);
    bool listen(const QString &path);
    void setOtp(bool on) { m_otp = on; }
    void setDelayMs(int ms) { m_delay = ms; }
    QStringList startedCommand() const { return m_cmd; }
    QString startedUser() const { return m_user; }
signals:
    void sessionStartRequested(const QString &user, const QStringList &cmd);

private:
    void onConnection();
    void onData();
    void reply(const QJsonObject &o, int delayMs = 0);
    void handle(const QJsonObject &o);
    QLocalServer m_server;
    QLocalSocket *m_sock = nullptr;
    QByteArray m_buf;
    QString m_user, m_pending;
    QStringList m_cmd;
    int m_step = 0;  // 0 idle, 1 waiting password, 2 waiting otp, 3 authenticated
    bool m_otp = false;
    int m_delay = 600;
};
