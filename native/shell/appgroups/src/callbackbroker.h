// SPDX-License-Identifier: AGPL-3.0-or-later
// The host end of a sign-in callback forward. The browser's group reaches 127.0.0.1:PORT on the host; this listener
// takes that connection, reads the HTTP request line and hands the request on to the app's group only when it is a GET
// for the exact path the sign-in named in its redirect_uri. Anything else gets a 404 and is closed, and does not count
// as the callback. The hand-over goes over a unix socket that only the person's own uid can open.
//
// Why loopback on the host is acceptable for the browser-facing side: that connection comes from the container
// runtime's proxy, which runs as another uid, so its peer cannot be told from another local user's. What another local
// user can do there is limited to sending this one request path once; the sign-in's own state/PKCE check at the app
// still has to pass, and a port somebody else already holds is refused before anything is forwarded.
#pragma once
#include <QByteArray>
#include <QString>

namespace TaosAppGroups {

// "GET <target> HTTP/1.x" whose path (before ? and #) equals expectedPath exactly (percent-decoded, no dot segments).
bool callbackRequestMatches(const QByteArray &requestHead, const QString &expectedPath);

// Binds 127.0.0.1:port (only that address) and listens. Returns the descriptor, or -1 with *error set.
int listenCallbackPort(int port, QString *error);

// Serves the listener for up to `seconds`. Returns true once one matching request was relayed to the unix socket
// `upstream` and its answer sent back; false on timeout. Non-matching connections are answered 404 and closed.
bool serveCallback(int listenFd, const QString &expectedPath, const QString &upstream, int seconds);

} // namespace TaosAppGroups
