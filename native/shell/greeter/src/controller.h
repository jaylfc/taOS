#pragma once
#include <QObject>
#include <QStringList>
#include <QUrl>

#include "greetdclient.h"

// GreeterController: what the login UI talks to. It turns the GreetdClient
// conversation into a small set of properties and signals.
class GreeterController : public QObject {
    Q_OBJECT
    Q_PROPERTY(int phase READ phase NOTIFY phaseChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY phaseChanged)
    Q_PROPERTY(QString selectedUser READ selectedUser NOTIFY selectedUserChanged)
    Q_PROPERTY(QString placeholder READ placeholder NOTIFY promptChanged)
    Q_PROPERTY(bool promptSecret READ promptSecret NOTIFY promptChanged)
    Q_PROPERTY(QString message READ message NOTIFY messageChanged)
    Q_PROPERTY(QString messageKind READ messageKind NOTIFY messageChanged)  // "", "error", "info"
    Q_PROPERTY(QString hostname READ hostname CONSTANT)
    Q_PROPERTY(QUrl wallpaper READ wallpaper CONSTANT)
    Q_PROPERTY(bool demo READ demo CONSTANT)
    Q_PROPERTY(bool clock24 READ clock24 CONSTANT)
public:
    enum Phase { Idle, Preparing, Input, Busy, Done, Fatal };
    Q_ENUM(Phase)

    GreeterController(GreetdClient *client, QObject *parent = nullptr);
    void setHostname(const QString &h) { m_host = h; }
    void setWallpaper(const QUrl &u) { m_wall = u; }
    void setDemo(bool d) { m_demo = d; }
    void setClock24(bool b) { m_clock24 = b; }
    bool clock24() const { return m_clock24; }

    int phase() const { return m_phase; }
    bool busy() const { return m_phase == Busy || m_phase == Preparing; }
    QString selectedUser() const { return m_user; }
    QString placeholder() const { return m_placeholder; }
    bool promptSecret() const { return m_secret; }
    QString message() const { return m_message; }
    QString messageKind() const { return m_messageKind; }
    QString hostname() const { return m_host; }
    QUrl wallpaper() const { return m_wall; }
    bool demo() const { return m_demo; }

    Q_INVOKABLE void select(const QString &username);
    Q_INVOKABLE void submit(const QString &text);
    Q_INVOKABLE void back();
    // Reports a startup problem (no GREETD_SOCK, no connection).
    void reportFatal(const QString &message);

    static QString friendlyPrompt(const QString &text);

signals:
    void phaseChanged();
    void selectedUserChanged();
    void promptChanged();
    void messageChanged();
    void failed();           // wrong credentials: shake and clear
    void inputReady();       // the field may take focus
    void sessionStarted();
    void fatalError(const QString &message);

private:
    void setPhase(Phase p);
    void setMessage(const QString &kind, const QString &text);

    GreetdClient *m_client;
    Phase m_phase = Idle;
    QString m_user, m_placeholder, m_message, m_messageKind, m_host;
    QString m_lastKind;
    bool m_secret = true;
    bool m_havePending = false;
    QString m_pending;
    QUrl m_wall;
    bool m_demo = false;
    bool m_clock24 = true;
};
