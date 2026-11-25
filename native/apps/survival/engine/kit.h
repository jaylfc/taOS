// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Grab-and-go kit templates (a JSON manifest in the repository) checked against what this machine has. A check is
// "ok", "missing" or "unknown": a thing the engine cannot detect is never shown as missing or as present.
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace Taos::Survival {

struct KitContext
{
    int zimPacks = 0;
    int mapPacks = 0;
    qint64 packBytes = 0;
    int radiosPossible = 0;       // USB serial devices that could be a radio
    int radiosConfirmed = 0;      // answered a handshake
    int batteries = -1;           // -1: could not be read
    qint64 freeBytes = -1;        // free space where packs go; -1: unknown
    int positionSources = -1;     // -1: could not be read
    QString arch;
};

/// The templates evaluated against `ctx`: [{id, title, hardware, summary, ready, total, items:[{id, title, detail,
/// status}]}], `ready` counting the items that are ok.
QJsonArray evaluateKits(const QJsonObject &manifest, const KitContext &ctx);

} // namespace Taos::Survival
