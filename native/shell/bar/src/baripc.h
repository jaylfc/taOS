#pragma once
#include <QLocalServer>
#include <QObject>

class Audio;

// `taos-bar volume up|down|mute|mic-mute` (and `taos-bar mic-mute`) talk to the
// running bar through this socket, so a window manager key binding can change
// the volume and the bar can flash the level. One line in, one line out.
class BarIpc : public QObject {
    Q_OBJECT
public:
    explicit BarIpc(Audio *audio, QObject *parent = nullptr);
    // `path` empty: $TAOS_BAR_SOCKET, else $XDG_RUNTIME_DIR/taos-bar.sock.
    bool listen(const QString &path = QString());
    QString path() const { return m_server.fullServerName(); }
    static QString defaultPath();
    // Runs a command string such as "volume up". Returns "ok" or "error: ...".
    QString run(const QString &line);
    // The client side: returns the process exit code (0 ok, 1 not running or refused).
    static int send(const QStringList &args, const QString &path = QString(), QString *message = nullptr);

private:
    Audio *m_audio;
    QLocalServer m_server;
};
