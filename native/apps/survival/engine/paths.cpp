// SPDX-License-Identifier: AGPL-3.0-or-later
#include "paths.h"

#include <QFile>
#include <QStandardPaths>

namespace Taos::Survival {

QString survivalDataDir()
{
    const QString env = qEnvironmentVariable("TAOS_SURVIVAL_DIR");
    if (!env.isEmpty())
        return env;
    return QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation) + QStringLiteral("/taos/survival");
}

QByteArray bundledCatalog(const QString &name)
{
    QFile f(QStringLiteral(":/taos-survival/catalog/") + name);
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}

} // namespace Taos::Survival
