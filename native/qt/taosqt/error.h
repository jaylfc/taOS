// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonObject>
#include <QJsonValue>
#include <QMetaType>
#include <QString>

#include "taosqt/global.h"

namespace Taos {

/// What went wrong, mapped from the controller's answer (or the lack of one).
///
/// `message()` is the controller's own words when it sent any (the text of a
/// `{"detail": ...}` or `{"error": ...}` body), otherwise a transport detail.
/// `userMessage()` is a sentence fit for a status line or dialog; apps should
/// show it and never build their own text from `kind()`.
class TAOSQT_EXPORT Error
{
    Q_GADGET
    Q_PROPERTY(Kind kind READ kind)
    Q_PROPERTY(int httpStatus READ httpStatus)
    Q_PROPERTY(QString message READ message)
    Q_PROPERTY(QString userMessage READ userMessage)
    Q_PROPERTY(bool isError READ isError)
public:
    enum Kind {
        None,            ///< no error
        Unauthenticated, ///< 401: not signed in, session expired or token refused
        Forbidden,       ///< 403
        NotFound,        ///< 404
        Conflict,        ///< 409 (for example a decision that was already answered)
        RateLimited,     ///< 429, see retryAfterMs()
        Validation,      ///< 422, see fields()
        BadRequest,      ///< 400
        Http,            ///< any other 4xx
        Server,          ///< 5xx
        Network,         ///< the controller could not be reached
        Decode,          ///< the answer was not what the client expected
        Stream,          ///< an `event: error` frame on a stream
        Cancelled,       ///< the request was aborted by the caller (or the client was reset)
        Internal         ///< a bug or an invalid call (unknown op, bad arguments)
    };
    Q_ENUM(Kind)

    Error() = default;
    Error(Kind kind, QString message, int httpStatus = 0)
        : m_kind(kind), m_message(std::move(message)), m_status(httpStatus) {}

    /// Build from the `{"kind","message","status",...}` object of the FFI layer.
    static Error fromJson(const QJsonObject &o);

    Kind kind() const { return m_kind; }
    bool isError() const { return m_kind != None; }
    int httpStatus() const { return m_status; }
    QString message() const { return m_message; }
    /// For RateLimited: how long the controller asked us to wait, or 0.
    int retryAfterMs() const { return m_retryAfterMs; }
    /// For Validation: the failing fields, `[{"location","message"}]`.
    QJsonValue fields() const { return m_fields; }
    /// True when the same call may succeed later (Network, Server, RateLimited).
    bool isRetryable() const;
    /// A plain-words sentence for the UI.
    QString userMessage() const;

private:
    Kind m_kind = None;
    QString m_message;
    int m_status = 0;
    int m_retryAfterMs = 0;
    QJsonValue m_fields;
};

/// The outcome of one request: a JSON value, or an Error.
struct TAOSQT_EXPORT Result
{
    QJsonValue value; ///< the response (Null for calls with no body)
    Error error;
    bool ok() const { return !error.isError(); }
};

} // namespace Taos

Q_DECLARE_METATYPE(Taos::Result)
