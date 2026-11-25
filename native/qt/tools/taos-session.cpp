// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-session: the command line face of the desktop's shared taOS sign-in.
//
//   taos-session status              signed in as <user> / signed out / controller unreachable
//   taos-session login [--user U]    read the password from stdin (one line), sign in, store the session
//   taos-session logout              sign out at the controller, then remove the stored session
//
// Exit status: 0 signed in (or done), 1 signed out / refused, 2 controller unreachable, 3 bad usage.
// The session lives in $XDG_RUNTIME_DIR/taos/session (directory 0700, file 0600). The token is never
// printed, never taken from argv or the environment, and never logged.
#include <QCoreApplication>
#include <QFile>
#include <QTextStream>
#include <QTimer>

#include <cstdio>

#include "taosqt/desktopsession.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;

namespace {
QTextStream &out()
{
    static QTextStream s(stdout);
    return s;
}
constexpr int kTimeoutMs = 15000;
// exit() before the event loop runs does nothing, and the token source may answer inside start().
void finish(int code) { QMetaObject::invokeMethod(QCoreApplication::instance(), [code] { QCoreApplication::exit(code); }, Qt::QueuedConnection); }
} // namespace

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QStringList args = app.arguments().mid(1);
    if (args.isEmpty() || (args.first() != u"status"_s && args.first() != u"login"_s && args.first() != u"logout"_s)) {
        QTextStream(stderr) << "usage: taos-session status | login [--user NAME] (password on stdin) | logout\n";
        return 3;
    }
    const QString cmd = args.first();
    QString user;
    const int ui = args.indexOf(u"--user"_s);
    if (ui >= 0 && ui + 1 < args.size())
        user = args.at(ui + 1);

    ClientOptions options;
    if (qEnvironmentVariableIsSet("TAOS_URL"))
        options.baseUrl = QUrl(qEnvironmentVariable("TAOS_URL"));
    Session session(options);
    auto *source = new DesktopSessionTokenSource;
    session.setTokenSource(source);
    if (source->path().isEmpty()) {
        QTextStream(stderr) << "taos-session: no XDG_RUNTIME_DIR, nothing can be stored\n";
        return 3;
    }

    QTimer::singleShot(kTimeoutMs, &app, [] { out() << "controller unreachable\n"; out().flush(); finish(2); });

    if (cmd == u"login"_s) {
        QFile in;
        in.open(stdin, QIODevice::ReadOnly);
        QString password = QString::fromUtf8(in.readLine()).trimmed();
        if (password.isEmpty()) {
            QTextStream(stderr) << "taos-session: the password is read from stdin\n";
            return 3;
        }
        QObject::connect(&session, &Session::signedIn, &app, [&] {
            out() << "signed in as " << session.userName() << "\n"; out().flush();
            QTimer::singleShot(200, &app, [] { QCoreApplication::exit(0); });
        });
        QObject::connect(&session, &Session::signInFailed, &app, [&](const Error &e) {
            out() << (e.kind() == Error::Network ? u"controller unreachable"_s : e.userMessage()) << "\n"; out().flush();
            finish(e.kind() == Error::Network ? 2 : 1);
        });
        session.signIn(user, password);
        password.fill(QLatin1Char('\0'));
        return app.exec();
    }

    // status and logout start from whatever is stored; a refused session is dropped by the token source.
    QObject::connect(&session, &Session::signInRequired, &app, [&] {
        out() << "signed out\n"; out().flush();
        finish(cmd == u"logout"_s ? 0 : 1);
    });
    QObject::connect(&session, &Session::signInFailed, &app, [&](const Error &e) {
        if (e.kind() == Error::Network && cmd == u"logout"_s) {
            // Cannot tell the controller: still remove the local copy, and say so.
            source->forget();
            out() << "controller unreachable: local session removed, the controller was not told\n"; out().flush();
            finish(2);
            return;
        }
        out() << (e.kind() == Error::Network ? u"controller unreachable"_s : e.userMessage()) << "\n"; out().flush();
        finish(e.kind() == Error::Network ? 2 : 1);
    });
    QObject::connect(&session, &Session::signedIn, &app, [&] {
        if (cmd == u"status"_s) {
            out() << "signed in as " << session.userName() << "\n"; out().flush();
            finish(0);
            return;
        }
        session.client()->call(u"auth.logout"_s)->then(&app, [&](const Result &r) {
            source->forget();
            session.client()->forgetCredentials();
            out() << (r.ok() ? u"signed out"_s : u"local session removed, the controller did not confirm"_s) << "\n"; out().flush();
            finish(r.ok() ? 0 : 2);
        });
    });
    session.start();
    return app.exec();
}
