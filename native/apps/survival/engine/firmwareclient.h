// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The app's end of the taos-firmware local API (native/firmware/README.md): HTTP on 127.0.0.1 with a bearer token
// read from the engine's data directory. Asynchronous. It only ever talks to the loopback address, and it flashes
// nothing by itself: the guarded flow in the UI calls plan first and run only after the person typed the board name.
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QPointer>
#include <functional>

namespace Taos::Survival {

struct FwReply
{
    bool reached = false;    // the engine answered at all
    int http = 0;
    QJsonObject json;        // the reply body ({ok, ...})
    QString error;           // plain words when not reached or when the engine refused
};

class FirmwareClient : public QObject
{
    Q_OBJECT
public:
    explicit FirmwareClient(QObject *parent = nullptr);
    /// $TAOS_FIRMWARE_DIR, else $XDG_DATA_HOME/taos/firmware, else ~/.local/share/taos/firmware.
    static QString defaultDir();
    /// $TAOS_FIRMWARE_PORT, else 7421.
    static int defaultPort();
    void setDir(const QString &d) { m_dir = d; }
    void setPort(int p) { m_port = p; }
    using Callback = std::function<void(const FwReply &)>;
    /// Asks the engine for a single-use permission to do one thing the person just asked for (accept a licence, flash a
    /// board), over its private owner socket: the engine checks with the kernel that this process runs as the owner.
    /// The nonce is scoped to `action` and `subject`, handed to the callback once and never kept here.
    void mintNonce(const QString &action, const QString &subject, QObject *ctx, std::function<void(const QString &nonce, const QString &error)> cb);
    void request(const QString &method, const QString &path, const QJsonObject &body, QObject *ctx, Callback cb, int timeoutMs = 20000);

private:
    QNetworkAccessManager m_nam;
    QString m_dir;
    int m_port;
};

} // namespace Taos::Survival
