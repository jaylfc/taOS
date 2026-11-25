// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A small QR Code encoder (ISO/IEC 18004): byte mode, error correction level L or M, versions 1 to 40, all eight masks
// tried and the lowest penalty kept. It exists so a channel can be shared as a picture on screen with no network and
// no extra library. Written from the public specification; checked against an independent encoder in the tests.
#include <QByteArray>
#include <QStringList>

namespace Taos::Survival {

struct QrResult
{
    bool ok = false;
    int size = 0;                 // modules per side
    int version = 0;
    QList<QList<bool>> modules;   // [row][column], true is dark
    QString error;
};

/// Encodes `data`; `eccHigh` selects level M (more repair) instead of L. Fails in words when the data does not fit.
QrResult qrEncode(const QByteArray &data, bool eccHigh = false);
/// One string of '0' and '1' per row, for the tests and for QML.
QStringList qrRows(const QrResult &r);

} // namespace Taos::Survival
