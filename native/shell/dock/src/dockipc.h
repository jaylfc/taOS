#pragma once
#include <QLocalServer>
#include <QObject>

// The running dock listens on $XDG_RUNTIME_DIR/taos-shell/dock.sock (the shell's folder). `taos-dock
// reveal|hide|toggle|focus` writes one word to it; sway binds Super+D to `taos-dock focus`.
class DockIpc : public QObject {
    Q_OBJECT
public:
    explicit DockIpc(QObject *parent = nullptr);
    static QString socketPath();
    static bool isCommand(const QString &w);
    bool listen(const QString &path = QString());
    // Client side: false (and `error`) when no dock answers.
    static bool send(const QString &command, QString *error, const QString &path = QString());
signals:
    void command(const QString &word);
private:
    QLocalServer m_server;
};
