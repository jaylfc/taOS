#include "taosqt/error.h"

#include <QJsonArray>

using namespace Qt::StringLiterals;

namespace Taos {

static Error::Kind kindFromName(const QString &k)
{
    static const QHash<QString, Error::Kind> map = {
        {u"unauthenticated"_s, Error::Unauthenticated}, {u"forbidden"_s, Error::Forbidden},
        {u"not_found"_s, Error::NotFound},     {u"conflict"_s, Error::Conflict},
        {u"rate_limited"_s, Error::RateLimited}, {u"validation"_s, Error::Validation},
        {u"bad_request"_s, Error::BadRequest}, {u"http"_s, Error::Http},
        {u"server"_s, Error::Server},          {u"network"_s, Error::Network},
        {u"decode"_s, Error::Decode},          {u"stream"_s, Error::Stream},
        {u"cancelled"_s, Error::Cancelled},
    };
    return map.value(k, Error::Internal);
}

Error Error::fromJson(const QJsonObject &o)
{
    Error e(kindFromName(o.value(u"kind"_s).toString()), o.value(u"message"_s).toString(),
            o.value(u"status"_s).toInt());
    e.m_retryAfterMs = o.value(u"retry_after_ms"_s).toInt();
    e.m_fields = o.value(u"fields"_s);
    return e;
}

bool Error::isRetryable() const
{
    return m_kind == Network || m_kind == Server || m_kind == RateLimited;
}

QString Error::userMessage() const
{
    // The controller's own sentence is the best text when it sent one.
    const QString said = m_message.trimmed();
    switch (m_kind) {
    case None:
        return {};
    case Unauthenticated:
        return said.isEmpty() ? QObject::tr("Please sign in again.") : said;
    case Forbidden:
        return said.isEmpty() ? QObject::tr("You are not allowed to do that.") : said;
    case NotFound:
        return said.isEmpty() ? QObject::tr("That no longer exists.") : said;
    case Conflict:
        return said.isEmpty() ? QObject::tr("That changed in the meantime. Refresh and try again.") : said;
    case RateLimited:
        return m_retryAfterMs > 0
            ? QObject::tr("Too many attempts. Try again in %1 seconds.").arg((m_retryAfterMs + 999) / 1000)
            : QObject::tr("Too many attempts. Try again in a moment.");
    case Validation:
    case BadRequest:
    case Http:
        return said.isEmpty() ? QObject::tr("The controller did not accept that.") : said;
    case Server:
        return said.isEmpty() ? QObject::tr("The controller had a problem. Try again in a moment.")
                              : QObject::tr("The controller had a problem: %1").arg(said);
    case Network:
        return QObject::tr("Can't reach the controller. Retrying.");
    case Decode:
        return QObject::tr("The controller sent something this app does not understand.");
    case Stream:
        return said.isEmpty() ? QObject::tr("The live feed reported a problem.") : said;
    case Cancelled:
        return QObject::tr("Cancelled.");
    case Internal:
        break;
    }
    return said.isEmpty() ? QObject::tr("Something went wrong.") : said;
}

} // namespace Taos
