// SPDX-License-Identifier: AGPL-3.0-or-later
// Writes the small test library (lib.zim) and test map (area.pmtiles) used by the tests into a folder, so a picture of the app
// shows the map and library sections the way a person with packs installed sees them. Nothing is downloaded.
#include <QCoreApplication>
#include <QDir>

#include "fixtures.h"

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (argc < 2)
        return 2;
    const QString d = QString::fromLocal8Bit(argv[1]);
    QDir().mkpath(d);
    return Fixture::writeZim(d + QStringLiteral("/lib.zim")) && Fixture::writePmtiles(d + QStringLiteral("/area.pmtiles")) ? 0 : 1;
}
