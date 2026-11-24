// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-notifyd: the notification server of the native desktop. Owns org.freedesktop.Notifications
// on the person's session bus and forwards each notification to their controller.
#include <QCoreApplication>
#include <QDBusConnection>
#include <QDir>
#include <QSocketNotifier>
#include <QStandardPaths>
#include <QTextStream>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "buffer.h"
#include "forwarder.h"
#include "service.h"

using namespace Taos::Notifyd;
using namespace Qt::StringLiterals;

static int g_sig[2] = {-1, -1};
static void onSignal(int) { const char c = 1; const ssize_t r = write(g_sig[1], &c, 1); (void)r; }

int main(int argc, char **argv)
{
    if (argc >= 2 && QByteArray(argv[1]) == "--version") {
        printf("taos-notifyd %s\n", TAOS_NOTIFYD_VERSION);
        return 0;
    }
    QCoreApplication app(argc, argv);
    QTextStream err(stderr);

    // State directory: the person's own ($XDG_STATE_HOME or ~/.local/state), never shared.
    QString state = qEnvironmentVariable("TAOS_NOTIFYD_STATE_DIR");
    if (state.isEmpty()) {
        QString base = qEnvironmentVariable("XDG_STATE_HOME");
        if (base.isEmpty())
            base = QDir::homePath() + u"/.local/state"_s;
        state = base + u"/taos/notifyd"_s;
    }
    WriteAheadBuffer::Options bo;
    bo.dir = state;
    WriteAheadBuffer buffer(bo);
    QString why;
    if (!buffer.open(&why)) {
        err << "taos-notifyd: " << why << "\n";
        return 1; // the supervisor retries with backoff
    }

    Forwarder::Options fo;
    fo.runtimeDir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    Forwarder fwd(&buffer, fo);
    Service svc(&buffer, &fwd, DropList(qEnvironmentVariable("TAOS_NOTIFYD_DROP_ENTRIES")));

    QDBusConnection bus = QDBusConnection::sessionBus();
    if (!svc.start(bus, &why)) {
        err << "taos-notifyd: " << why << "\n";
        // Another server owns the name, or there is no bus: a permanent condition, not a crash.
        svc.writeStatus(u"idle"_s, why);
        return bus.isConnected() ? 0 : 1;
    }

    // The session ends: the bus goes away, or the supervisor sends TERM. The buffer is already on
    // disk, so there is nothing to flush; an unconfirmed send is repeated next time (idempotent).
    bus.connect(QString(), u"/org/freedesktop/DBus/Local"_s, u"org.freedesktop.DBus.Local"_s, u"Disconnected"_s,
                &app, SLOT(quit()));
    if (::pipe(g_sig) == 0) {
        auto *sn = new QSocketNotifier(g_sig[0], QSocketNotifier::Read, &app);
        QObject::connect(sn, &QSocketNotifier::activated, &app, [] { QCoreApplication::quit(); });
        std::signal(SIGTERM, onSignal);
        std::signal(SIGINT, onSignal);
        std::signal(SIGHUP, onSignal);
    }
    svc.writeStatus(u"serving"_s);
    err << "taos-notifyd " << TAOS_NOTIFYD_VERSION << ": serving org.freedesktop.Notifications, "
        << buffer.count() << " buffered\n";
    fwd.kick(); // anything left over from an earlier run
    const int rc = app.exec();
    bus.unregisterObject(u"/org/freedesktop/Notifications"_s);
    return rc;
}
