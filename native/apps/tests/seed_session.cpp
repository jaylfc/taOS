// Test helper: sign in with a password and write the shared desktop session file
// ($XDG_RUNTIME_DIR/taos/session) the way a first sign-in does, so the real app
// executables start signed in (screenshots, manual runs).
//   seed_session <controller url> <user> <password>   (XDG_RUNTIME_DIR must exist, mode 0700)
#include <QCoreApplication>
#include <QTimer>

#include "taosqt/desktopsession.h"
#include "taosqt/session.h"

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (argc < 4)
        return 2;
    Taos::ClientOptions o;
    o.baseUrl = QUrl(QString::fromLocal8Bit(argv[1]));
    Taos::Session s(o);
    s.setTokenSource(new Taos::DesktopSessionTokenSource);
    QObject::connect(&s, &Taos::Session::stateChanged, &app, [&] {
        if (s.state() == Taos::Session::SignedIn)
            QTimer::singleShot(300, &app, [&app] { app.exit(0); });
    });
    QObject::connect(&s, &Taos::Session::signInFailed, &app, [&app] { app.exit(1); });
    s.start();
    s.signIn(QString::fromLocal8Bit(argv[2]), QString::fromLocal8Bit(argv[3]));
    QTimer::singleShot(20000, &app, [&app] { app.exit(3); });
    return app.exec();
}
