// SPDX-License-Identifier: AGPL-3.0-or-later
#include "firmwareclient.h"

#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QStandardPaths>
#include <QUrl>

using namespace Qt::StringLiterals;

namespace Taos::Survival {

FirmwareClient::FirmwareClient(QObject *parent) : QObject(parent), m_dir(defaultDir()), m_port(defaultPort()) {}

QString FirmwareClient::defaultDir()
{
    const QByteArray e = qgetenv("TAOS_FIRMWARE_DIR");
    if (!e.isEmpty())
        return QString::fromLocal8Bit(e);
    const QByteArray x = qgetenv("XDG_DATA_HOME");
    return (x.isEmpty() ? QDir::homePath() + u"/.local/share"_s : QString::fromLocal8Bit(x)) + u"/taos/firmware"_s;
}

int FirmwareClient::defaultPort()
{
    bool ok = false;
    const int p = qEnvironmentVariableIntValue("TAOS_FIRMWARE_PORT", &ok);
    return ok && p > 0 && p < 65536 ? p : 7421;
}

void FirmwareClient::request(const QString &method, const QString &path, const QJsonObject &body, QObject *ctx, Callback cb, int timeoutMs)
{
    QFile tf(m_dir + u"/api-token"_s);
    QByteArray token;
    if (tf.open(QIODevice::ReadOnly))
        token = tf.readAll().trimmed();
    if (token.isEmpty()) {
        FwReply r;
        r.error = tr("The firmware manager is not running. Start it, then try again.");
        if (cb)
            cb(r);
        return;
    }
    QUrl url;
    url.setScheme(u"http"_s);
    url.setHost(u"127.0.0.1"_s);
    url.setPort(m_port);
    url.setPath(path.section(QLatin1Char('?'), 0, 0));
    if (path.contains(QLatin1Char('?')))
        url.setQuery(path.section(QLatin1Char('?'), 1));
    QNetworkRequest req(url);
    req.setRawHeader("Authorization", "Bearer " + token);
    req.setHeader(QNetworkRequest::ContentTypeHeader, u"application/json"_s);
    req.setTransferTimeout(timeoutMs);
    QNetworkReply *rep = method == u"GET"_s ? m_nam.get(req) : m_nam.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    QPointer<QObject> guard(ctx);
    connect(rep, &QNetworkReply::finished, this, [rep, guard, cb]() {
        FwReply r;
        r.http = rep->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        r.reached = r.http != 0;
        const QByteArray data = rep->readAll();
        const QJsonDocument d = QJsonDocument::fromJson(data);
        if (d.isObject())
            r.json = d.object();
        if (!r.reached)
            r.error = tr("The firmware manager did not answer: %1").arg(rep->errorString());
        else if (r.json.isEmpty())
            r.error = tr("The firmware manager sent something unreadable.");
        else if (!r.json.value(u"ok"_s).toBool(true))
            r.error = r.json.value(u"message"_s).toString().isEmpty() ? tr("The firmware manager refused.") : r.json.value(u"message"_s).toString();
        rep->deleteLater();
        if (guard && cb)
            cb(r);
    });
}

} // namespace Taos::Survival
