#pragma once
#include <QByteArray>
#include <QString>

namespace TaosWin {

// The i3/sway IPC wire format over a plain blocking unix socket: "i3-ipc", payload length (u32),
// message type (u32), payload. No event loop and no Qt object is needed, so the CLI, a GUI thread and the tests
// all use the same code. Timeouts are in milliseconds.
namespace Ipc {
enum Type : quint32 {
    RunCommand = 0, GetWorkspaces = 1, Subscribe = 2, GetOutputs = 3, GetTree = 4,
    EventWorkspace = 0x80000000u, EventWindow = 0x80000003u, EventShutdown = 0x80000006u,
};
QByteArray encode(quint32 type, const QByteArray &payload);
// Reads one message out of `buf` (and removes it). False when `buf` holds no whole message yet.
bool take(QByteArray *buf, quint32 *type, QByteArray *payload);
}  // namespace Ipc

class SwayConnection {
public:
    SwayConnection() = default;
    ~SwayConnection() { close(); }
    SwayConnection(const SwayConnection &) = delete;
    SwayConnection &operator=(const SwayConnection &) = delete;

    // $SWAYSOCK, else $I3SOCK, else empty.
    static QString socketPath();
    bool open(const QString &path, QString *error = nullptr);   // empty path: socketPath()
    void close();
    bool isOpen() const { return m_fd >= 0; }
    int fd() const { return m_fd; }

    // Sends one request and returns its reply. Event messages that arrive first are skipped.
    bool request(quint32 type, const QByteArray &payload, QByteArray *reply, QString *error = nullptr, int timeoutMs = 4000);
    // For an event connection: the next message, or false on timeout (`*closed` set when the socket died).
    bool next(quint32 *type, QByteArray *payload, int timeoutMs, bool *closed = nullptr);

private:
    bool fill(int timeoutMs, bool *closed);
    int m_fd = -1;
    QByteArray m_buf;
};

}  // namespace TaosWin
