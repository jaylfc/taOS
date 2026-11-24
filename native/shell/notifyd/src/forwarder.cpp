// SPDX-License-Identifier: AGPL-3.0-or-later
#include "forwarder.h"

#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QNetworkRequest>

#include <unistd.h>

using namespace Qt::StringLiterals;

namespace Taos::Notifyd {

Credentials readCredentials(const QString &runtimeDir)
{
    Credentials c;
    if (runtimeDir.isEmpty())
        return c;
    const QString path = QDir(runtimeDir).filePath(u"taos/session"_s);
    const QFileInfo fi(path);
    if (!fi.exists() || !fi.isFile() || fi.isSymLink())
        return c;
    // Never trust a file someone else owns or can read (same rule as the apps).
    if (fi.ownerId() != ::getuid() || (fi.permissions() & (QFileDevice::ReadGroup | QFileDevice::WriteGroup | QFileDevice::ExeGroup
                                                          | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther)))
        return c;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return c;
    const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
    c.baseUrl = o.value(u"base_url"_s).toString();
    c.userAgent = o.value(u"user_agent"_s).toString();
    c.session = o.value(u"session"_s).toString();
    c.csrf = o.value(u"csrf"_s).toString();
    return c;
}

Verdict classify(int status, const QByteArray &body)
{
    if (status == 200) {
        const QJsonDocument d = QJsonDocument::fromJson(body);
        // A 200 that is not the route's answer (a captive portal, a proxy) is not a confirmation.
        return d.isObject() && d.object().contains(u"id"_s) ? Verdict::Confirm : Verdict::RetryBackoff;
    }
    if (status == 400 || status == 409 || status == 413 || status == 422)
        return Verdict::Quarantine;
    if (status == 429)
        return Verdict::RetryAfter;
    return Verdict::RetryBackoff; // 401, 403, 404, 5xx, anything else: keep it
}

Forwarder::Forwarder(WriteAheadBuffer *buffer, const Options &o, QObject *parent)
    : QObject(parent), m_buf(buffer), m_o(o), m_backoff(o.backoffMinMs)
{
    m_timer.setSingleShot(true);
    connect(&m_timer, &QTimer::timeout, this, &Forwarder::pump);
}

void Forwarder::kick()
{
    if (!m_inflight && !m_timer.isActive())
        QTimer::singleShot(0, this, &Forwarder::pump);
}

void Forwarder::retryLater(int ms, const QString &why)
{
    m_status = why;
    m_timer.start(ms);
}

void Forwarder::pump()
{
    if (m_inflight)
        return;
    retryWarnings(); // a refusal that could not be warned about yet
    const QList<WriteAheadBuffer::Entry> all = m_buf->pending();
    if (all.isEmpty()) {
        m_status = u"idle"_s;
        Q_EMIT idle();
        return;
    }
    const Credentials c = readCredentials(m_o.runtimeDir);
    if (!c.valid()) {
        retryLater(m_backoff, u"waiting for the desktop sign-in"_s);
        m_backoff = qMin(m_backoff * 2, m_o.backoffMaxMs);
        return;
    }
    send(all.first(), c, false);
}

void Forwarder::send(const WriteAheadBuffer::Entry &e, const Credentials &c, bool csrfReplay)
{
    m_inflight = true;
    QNetworkRequest req(QUrl(c.baseUrl + u"/api/notifications/os"_s));
    req.setHeader(QNetworkRequest::UserAgentHeader, c.userAgent);
    req.setHeader(QNetworkRequest::ContentTypeHeader, u"application/json"_s);
    req.setTransferTimeout(m_o.requestTimeoutMs);
    const QString csrf = m_csrf.isEmpty() ? c.csrf : m_csrf;
    QString cookie = u"taos_session="_s + c.session;
    if (!csrf.isEmpty()) {
        cookie += u"; csrf_token="_s + csrf;
        req.setRawHeader("X-CSRF-Token", csrf.toUtf8());
    }
    req.setRawHeader("Cookie", cookie.toUtf8());
    QNetworkReply *r = m_net.post(req, QJsonDocument(e.record).toJson(QJsonDocument::Compact));
    connect(r, &QNetworkReply::finished, this, [=, this] {
        r->deleteLater();
        m_inflight = false;
        const int status = r->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const QByteArray body = r->readAll();
        if (status == 0) { // no answer at all: controller down, timeout, refused
            retryLater(m_backoff, u"controller unreachable"_s);
            m_backoff = qMin(m_backoff * 2, m_o.backoffMaxMs);
            return;
        }
        // The CSRF middleware re-issues the token on the response that refused it: use it once.
        if (status == 403 && !csrfReplay) {
            for (const QByteArray &h : r->rawHeader("Set-Cookie").split('\n')) {
                if (h.startsWith("csrf_token=")) {
                    const QString fresh = QString::fromUtf8(h.mid(11).split(';').first());
                    if (!fresh.isEmpty() && fresh != csrf) {
                        m_csrf = fresh;
                        send(e, c, true);
                        return;
                    }
                }
            }
        }
        switch (classify(status, body)) {
        case Verdict::Confirm:
            if (!m_buf->confirm(e.seq)) {
                retryLater(m_backoff, u"could not delete a confirmed record"_s); // sent again later: idempotent
                return;
            }
            ++m_delivered;
            m_backoff = m_o.backoffMinMs;
            m_status = u"delivered"_s;
            QTimer::singleShot(0, this, &Forwarder::pump);
            break;
        case Verdict::Quarantine:
            if (quarantine(e, status)) {
                QTimer::singleShot(0, this, &Forwarder::pump);
            } else { // could not move it aside: keep it, back off, and raise no warning yet
                retryLater(m_backoff, u"could not move a refused record aside"_s);
                m_backoff = qMin(m_backoff * 2, m_o.backoffMaxMs);
            }
            break;
        case Verdict::RetryAfter: {
            bool ok = false;
            const int secs = r->rawHeader("Retry-After").toInt(&ok);
            retryLater(qBound(1, ok ? secs : 1, 3600) * 1000, u"rate limited by the controller"_s);
            break;
        }
        case Verdict::RetryBackoff:
            retryLater(m_backoff, u"controller answered %1"_s.arg(status));
            m_backoff = qMin(m_backoff * 2, m_o.backoffMaxMs);
            break;
        }
    });
}

bool Forwarder::quarantine(const WriteAheadBuffer::Entry &e, int status)
{
    // Kept (rejected/), and made visible: a critical warning record goes through the same buffer.
    const QString app = e.record.value(u"app_name"_s).toString();
    if (!m_buf->quarantine(e.seq))
        return false; // still pending; the warning is raised once, after it has really been set aside
    ++m_quarantined;
    if (e.record.value(u"category"_s).toString().startsWith(u"taos.notifyd."_s))
        return true; // a refused warning record does not raise another warning
    if (!raiseRejectedWarning(app, status)) {
        // Cannot number a warning now. The refused copy is in rejected/; remember it, say so in the log
        // and in the status file, and raise the warning as soon as the counter works again.
        m_unwarned.append({app, status});
        qWarning().noquote() << "taos-notifyd: a refused notification is in" << QDir(m_buf->dir()).filePath(u"rejected"_s)
                             << "but its warning could not be recorded:" << m_warnError;
    }
    Q_EMIT healthChanged();
    return true;
}

bool Forwarder::raiseRejectedWarning(const QString &app, int status)
{
    QJsonObject w;
    quint64 seq = 0;
    QString err;
    if (!m_buf->nextSeq(&seq, &err)) {
        m_warnError = err;
        return false;
    }
    w[u"id"_s] = m_buf->clientId(seq);
    w[u"app_name"_s] = u"taOS"_s;
    w[u"desktop_entry"_s] = u"taos-notifyd"_s;
    w[u"summary"_s] = u"A notification could not be delivered"_s;
    w[u"body"_s] = u"The controller refused a notification from %1 (HTTP %2). A copy was kept in %3."_s
                       .arg(app.isEmpty() ? u"an app"_s : app).arg(status).arg(QDir(m_buf->dir()).filePath(u"rejected"_s));
    w[u"urgency"_s] = u"critical"_s;
    w[u"category"_s] = u"taos.notifyd.rejected"_s;
    w[u"icon"_s] = QString();
    w[u"actions"_s] = QJsonArray();
    w[u"expire_timeout_ms"_s] = -1;
    w[u"transient"_s] = false;
    w[u"posted_at"_s] = QDateTime::currentSecsSinceEpoch();
    const auto r = m_buf->append(seq, true, w);
    if (!r.ok) {
        m_warnError = r.error;
        return false;
    }
    m_warnError.clear();
    return true;
}

void Forwarder::retryWarnings()
{
    bool changed = false;
    while (!m_unwarned.isEmpty() && raiseRejectedWarning(m_unwarned.first().app, m_unwarned.first().status)) {
        m_unwarned.removeFirst();
        changed = true;
    }
    if (changed)
        Q_EMIT healthChanged();
}

} // namespace Taos::Notifyd
