// SPDX-License-Identifier: AGPL-3.0-or-later
#include "lockgate.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>

using namespace Qt::StringLiterals;

namespace Taos::Notifyd {

LockState parseLockState(int httpStatus, const QByteArray &body)
{
    LockState s; // locked, until proven otherwise
    if (httpStatus != 200)
        return s;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(body, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject())
        return s;
    const QJsonObject o = d.object();
    if (o.size() != 2 || !o.contains(u"locked"_s) || !o.contains(u"session"_s))
        return s;
    const QJsonValue l = o.value(u"locked"_s), n = o.value(u"session"_s);
    if (!l.isBool() || !n.isDouble() || n.toDouble() != double(qint64(n.toDouble())))
        return s;
    s.locked = l.toBool();
    s.session = qint64(n.toDouble());
    return s;
}

LockLevel parseLockLevel(const QString &s)
{
    if (s == u"full"_s)
        return LockLevel::Full;
    if (s == u"summary"_s)
        return LockLevel::Summary;
    return LockLevel::Hidden;
}

PopupForm popupForm(bool locked, LockLevel level, const QString &title, const QString &body, int count,
                    const QString &deviceLabel)
{
    PopupForm f;
    if (!locked || level == LockLevel::Full) {
        f.kind = PopupForm::Full;
        f.title = title;
        f.body = body;
    } else if (level == LockLevel::Summary) {
        f.kind = PopupForm::Summary;
        f.summaryLine = u"%1 new from %2"_s.arg(count).arg(deviceLabel);
    }
    return f;
}

LockGate::LockGate(const QString &baseUrl, int timeoutMs, QObject *parent)
    : QObject(parent), m_url(baseUrl + u"/auth/lock-state"_s), m_timeout(timeoutMs)
{
}

void LockGate::refresh()
{
    QNetworkRequest req{QUrl(m_url)};
    req.setRawHeader("X-taOS-Console", "1");
    req.setTransferTimeout(m_timeout);
    QNetworkReply *r = m_net.get(req);
    connect(r, &QNetworkReply::finished, this, [this, r] {
        r->deleteLater();
        const int status = r->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const LockState next = parseLockState(status, r->error() == QNetworkReply::NoError ? r->readAll() : QByteArray());
        const bool was = m_state.locked;
        m_state = next;
        if (was != next.locked)
            Q_EMIT changed(next.locked);
    });
}

} // namespace Taos::Notifyd
