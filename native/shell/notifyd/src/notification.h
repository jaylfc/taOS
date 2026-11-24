// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// One accepted freedesktop notification, in the form the controller route
// POST /api/notifications/os takes (the record kept on disk IS that request body).
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>

namespace Taos::Notifyd {

// The route's limits (tinyagentos/routes/notifications.py). Text is cut to them
// BEFORE it is buffered, so a long body is never refused by the controller for ever.
constexpr int kMaxSummary = 256;
constexpr int kMaxBody = 2048;
constexpr int kMaxApp = 128;
constexpr int kMaxIcon = 256;
constexpr int kMaxActions = 8;
constexpr int kMaxKey = 64;

struct Notification
{
    quint32 fdId = 0;          ///< the id the app got back from Notify
    QString clientId;          ///< "n<seq>": unique per post, minted once at accept
    QString replacesClientId;  ///< "n<fd id>" of the first post of a replaced notification
    QString appName, desktopEntry, summary, body, category, icon;
    QString urgency = QStringLiteral("normal"); ///< low | normal | critical
    QList<QPair<QString, QString>> actions;
    int expireMs = -1;
    bool transient = false;
    qint64 postedAt = 0;

    bool critical() const { return urgency == QLatin1String("critical"); }
    /// The JSON body for the controller (limits already applied).
    QJsonObject toJson() const;
};

/// Markup the spec allows in a body (<b> <i> <u> <a> <img>) removed, entities decoded.
QString stripMarkup(const QString &s);
/// Cut to `max` characters with an ellipsis.
QString clip(const QString &s, int max);
/// Build a Notification from the arguments of org.freedesktop.Notifications.Notify.
Notification fromNotify(const QString &appName, quint32 fdId, quint64 seq, quint32 replacesId, const QString &appIcon,
                        const QString &summary, const QString &body, const QStringList &actions,
                        const QVariantMap &hints, int expireTimeout, qint64 now);
/// The key filters and logs use: the desktop entry, else the app name (lower case, ".desktop" removed).
QString appKey(const Notification &n);

} // namespace Taos::Notifyd
