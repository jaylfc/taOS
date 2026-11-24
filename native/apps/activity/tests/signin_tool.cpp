// signin_tool <url> <user> <password> <runtime-dir>
// Signs in once and writes the desktop session snapshot the apps share
// (<runtime-dir>/taos/session), so the real app can be started against a
// controller without a prompt. Test infrastructure only.
#include <QCoreApplication>
#include <QTimer>

#include "taosqt/desktopsession.h"
#include "taosqt/session.h"

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    if (argc < 5)
        return 2;
    Taos::ClientOptions o;
    o.baseUrl = QUrl(QString::fromLocal8Bit(argv[1]));
    Taos::Session s(o);
    s.setTokenSource(new Taos::DesktopSessionTokenSource(QString::fromLocal8Bit(argv[4])));
    QObject::connect(&s, &Taos::Session::signedIn, &app, [&] { QTimer::singleShot(200, &app, [&] { app.exit(0); }); });
    QObject::connect(&s, &Taos::Session::signInFailed, &app, [&] { app.exit(1); });
    s.signIn(QString::fromLocal8Bit(argv[2]), QString::fromLocal8Bit(argv[3]));
    QTimer::singleShot(15000, &app, [&] { app.exit(3); });
    return app.exec();
}
