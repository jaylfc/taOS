// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A fake /sys/class/drm: `card1-<connector>/modes` and `edid`.
#include <QByteArray>
#include <QDir>
#include <QFile>
#include <QTemporaryDir>

inline QByteArray makeEdid(int hMm, int vMm) {
    QByteArray e(128, '\0');
    e[54] = 0x01; e[55] = 0x1d;  // a pixel clock: the first descriptor is a timing
    e[66] = char(hMm & 0xff); e[67] = char(vMm & 0xff); e[68] = char(((hMm >> 8) << 4) | (vMm >> 8));
    e[21] = char(hMm / 10); e[22] = char(vMm / 10);
    return e;
}

inline void addConnector(const QString &root, const QString &name, const QByteArray &modes, const QByteArray &edid) {
    const QString d = root + "/card1-" + name;
    QDir().mkpath(d);
    QFile m(d + "/modes"); if (m.open(QIODevice::WriteOnly)) m.write(modes);
    if (!edid.isEmpty()) { QFile e(d + "/edid"); if (e.open(QIODevice::WriteOnly)) e.write(edid); }
}
