// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QString>

namespace Taos::Survival {

/// The person's survival data folder: $TAOS_SURVIVAL_DIR, else <data home>/taos/survival. Packs, markers and licence
/// records live here, never in the repository.
QString survivalDataDir();
/// A bundled JSON manifest (packs.json, kits.json) from the app's resources.
QByteArray bundledCatalog(const QString &name);

} // namespace Taos::Survival
