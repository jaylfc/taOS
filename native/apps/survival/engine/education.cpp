// SPDX-License-Identifier: AGPL-3.0-or-later
#include "education.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

namespace Taos::Survival {

bool validChannelId(const QString &id)
{
    static const QRegularExpression re(QStringLiteral("^[0-9a-f]{32}$"));
    return re.match(id).hasMatch();
}

QList<EduChannel> parseKolibriChannels(const QByteArray &json)
{
    QList<EduChannel> out;
    for (const QJsonValue &v : QJsonDocument::fromJson(json).array()) {
        const QJsonObject o = v.toObject();
        EduChannel c;
        c.id = o.value(QStringLiteral("id")).toString();
        c.name = o.value(QStringLiteral("name")).toString();
        c.language = o.value(QStringLiteral("language")).toString();
        c.description = o.value(QStringLiteral("description")).toString();
        c.version = QString::number(o.value(QStringLiteral("version")).toInt());
        c.lastPublished = o.value(QStringLiteral("last_published")).toString().left(10);
        c.bytes = qint64(o.value(QStringLiteral("published_size")).toDouble());
        c.resources = o.value(QStringLiteral("total_resource_count")).toInt();
        if (validChannelId(c.id) && !c.name.isEmpty())   // an id that is not 32 hex digits is never used
            out.append(c);
    }
    return out;
}

bool ChannelImporter::start(const QString &channelId, QString *error)
{
    if (!validChannelId(channelId)) { if (error) *error = QStringLiteral("That is not a channel id."); return false; }
    if (m_proc) { if (error) *error = QStringLiteral("A channel is already being imported."); return false; }
    m_id = channelId;
    m_step = 0;
    next();
    return true;
}

void ChannelImporter::next()
{
    static const char *verbs[] = {"importchannel", "importcontent"};
    m_proc = m_exec->makeProcess(QStringLiteral("kolibri"), {QStringLiteral("manage"), QLatin1String(verbs[m_step]), QStringLiteral("network"), m_id}, this);
    connect(m_proc, &QProcess::finished, this, [this](int code, QProcess::ExitStatus st) {
        const QString out = QString::fromUtf8(m_proc->readAll());
        m_proc->deleteLater();
        m_proc = nullptr;
        if (st != QProcess::NormalExit || code != 0) {
            Q_EMIT finished(m_id, false, QStringLiteral("Kolibri stopped: %1").arg(out.right(300).simplified()));
            return;
        }
        if (++m_step < 2) { next(); return; }
        Q_EMIT finished(m_id, true, QString());
    });
    connect(m_proc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError e) {
        if (e != QProcess::FailedToStart || !m_proc) return;
        m_proc->deleteLater();
        m_proc = nullptr;
        Q_EMIT finished(m_id, false, QStringLiteral("Kolibri is not available. It lives in the survival-studio container."));
    });
    m_proc->start();
}

void ChannelImporter::cancel()
{
    if (m_proc) m_proc->kill();
}

} // namespace Taos::Survival
