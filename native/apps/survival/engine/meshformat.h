// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// How times and signal figures are written for people. Unknown is said as unknown, never as zero or "now".
#include <QCoreApplication>
#include <QString>

namespace Taos::Survival {

/// "just now", "5 minutes ago", "2 hours ago", "3 days ago". `thenMs` <= 0 is unknown. A time in the future (the
/// sender's clock is ahead) is said so rather than shown as a negative age.
inline QString formatAgo(qint64 thenMs, qint64 nowMs)
{
    if (thenMs <= 0)
        return QCoreApplication::translate("MeshFormat", "time unknown");
    const qint64 d = nowMs - thenMs;
    if (d < -60 * 1000)
        return QCoreApplication::translate("MeshFormat", "its clock is ahead of this device");
    if (d < 60 * 1000)
        return QCoreApplication::translate("MeshFormat", "just now");
    const qint64 mins = d / 60000;
    if (mins < 60)
        return mins == 1 ? QCoreApplication::translate("MeshFormat", "1 minute ago") : QCoreApplication::translate("MeshFormat", "%1 minutes ago").arg(mins);
    const qint64 hours = mins / 60;
    if (hours < 48)
        return hours == 1 ? QCoreApplication::translate("MeshFormat", "1 hour ago") : QCoreApplication::translate("MeshFormat", "%1 hours ago").arg(hours);
    return hours / 24 == 1 ? QCoreApplication::translate("MeshFormat", "1 day ago") : QCoreApplication::translate("MeshFormat", "%1 days ago").arg(hours / 24);
}

} // namespace Taos::Survival
