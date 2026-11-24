// SPDX-License-Identifier: AGPL-3.0-or-later
#include "notification.h"

#include <QJsonArray>
#include <QRegularExpression>

using namespace Qt::StringLiterals;

namespace Taos::Notifyd {

QString clip(const QString &s, int max)
{
    if (s.size() <= max)
        return s;
    return s.left(max - 1) + QChar(0x2026);
}

QString stripMarkup(const QString &in)
{
    static const QRegularExpression tag(u"<[^>]*>"_s);
    QString s = in;
    s.remove(tag);
    s.replace(u"&lt;"_s, u"<"_s).replace(u"&gt;"_s, u">"_s).replace(u"&quot;"_s, u"\""_s)
        .replace(u"&apos;"_s, u"'"_s).replace(u"&amp;"_s, u"&"_s);
    return s;
}

QString appKey(const Notification &n)
{
    QString k = (n.desktopEntry.isEmpty() ? n.appName : n.desktopEntry).toLower();
    if (k.endsWith(u".desktop"_s))
        k.chop(8);
    return k;
}

QJsonObject Notification::toJson() const
{
    QJsonObject o;
    o[u"id"_s] = clientId;
    o[u"app_name"_s] = appName;
    o[u"desktop_entry"_s] = desktopEntry;
    o[u"summary"_s] = summary;
    o[u"body"_s] = body;
    o[u"urgency"_s] = urgency;
    o[u"category"_s] = category;
    o[u"icon"_s] = icon;
    QJsonArray a;
    for (const auto &p : actions)
        a.append(QJsonObject{{u"key"_s, p.first}, {u"label"_s, p.second}});
    o[u"actions"_s] = a;
    if (!replacesClientId.isEmpty())
        o[u"replaces"_s] = replacesClientId;
    o[u"expire_timeout_ms"_s] = expireMs;
    o[u"transient"_s] = transient;
    o[u"posted_at"_s] = postedAt;
    return o;
}

Notification fromNotify(const QString &appName, quint32 fdId, quint64 seq, quint32 replacesId, const QString &appIcon,
                        const QString &summary, const QString &body, const QStringList &actions,
                        const QVariantMap &hints, int expireTimeout, qint64 now)
{
    Notification n;
    n.fdId = fdId;
    n.clientId = u"n%1"_s.arg(seq);
    if (replacesId > 0)
        n.replacesClientId = u"n%1"_s.arg(replacesId);
    n.appName = clip(appName, kMaxApp);
    n.desktopEntry = clip(hints.value(u"desktop-entry"_s).toString(), kMaxApp);
    n.summary = clip(summary, kMaxSummary);
    n.body = clip(stripMarkup(body), kMaxBody);
    n.category = clip(hints.value(u"category"_s).toString(), 64);
    n.icon = clip(appIcon.isEmpty() ? hints.value(u"image-path"_s).toString() : appIcon, kMaxIcon);
    switch (hints.value(u"urgency"_s).toInt()) {
    case 0: n.urgency = u"low"_s; break;
    case 2: n.urgency = u"critical"_s; break;
    default: n.urgency = u"normal"_s;
    }
    // actions arrive as key, label, key, label ...
    for (int i = 0; i + 1 < actions.size() && n.actions.size() < kMaxActions; i += 2) {
        if (actions.at(i).isEmpty())
            continue;
        n.actions.append({clip(actions.at(i), kMaxKey), clip(actions.at(i + 1), 128)});
    }
    n.expireMs = expireTimeout < -1 ? -1 : qMin(expireTimeout, 86'400'000);
    n.transient = hints.value(u"transient"_s).toBool();
    n.postedAt = now;
    return n;
}

} // namespace Taos::Notifyd
