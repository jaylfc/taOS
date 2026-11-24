#pragma once
#include <QJsonValue>

#include "taosqt/client.h"

namespace Taos {

// Thin, typed-argument wrappers over Client::call() for the first apps. They
// add no state: construct one next to the Client, or per call. Each returns a
// Reply (see client.h). Results are the controller's JSON as normalised by the
// Rust client (field names are the controller's).

/// `/api/notifications`. Rows: {id, timestamp, level, title, message, read,
/// source, data, user_id, pinned}. The list holds every pinned grant row plus
/// the 20 newest others.
class TAOSQT_EXPORT Notifications
{
public:
    explicit Notifications(Client *c) : m_c(c) {}
    Reply *list(bool unreadOnly = false) const;
    Reply *markRead(qint64 id) const;
    Reply *markAllRead() const;                 ///< -> {marked}
    /// -> {outcome: "archived" | "kept_pending_request"} (a pending consent row
    /// is only marked read)
    Reply *archive(qint64 id) const;
    Reply *clear() const;                       ///< -> {archived, kept}
private:
    Client *m_c;
};

/// `/api/decisions`. `status`: "pending", "answered", "withdrawn" or empty.
class TAOSQT_EXPORT Decisions
{
public:
    explicit Decisions(Client *c) : m_c(c) {}
    Reply *list(const QString &status = {}, const QString &projectId = {}, int limit = 0) const;
    Reply *get(const QString &id) const;
    Reply *history(const QString &id) const;
    /// `value`: a string (option value, "approve", "deny", free text) or an
    /// array of strings (multi select). `other`: the "Other" text.
    Reply *answer(const QString &id, const QJsonValue &value, const QString &other = {},
                  const QString &note = {}) const;
    Reply *addNote(const QString &id, const QString &text) const;
private:
    Client *m_c;
};

/// The dashboard reads. `snapshot()` fetches activity, loaded models, scheduler
/// stats and workers together: {activity, loaded_models, scheduler, workers}.
class TAOSQT_EXPORT Dashboard
{
public:
    explicit Dashboard(Client *c) : m_c(c) {}
    Reply *snapshot() const;
    Reply *activity() const;
    Reply *loadedModels() const;
    Reply *schedulerStats() const;
    Reply *schedulerTasks(int limit = 50) const;
    Reply *workers() const;
private:
    Client *m_c;
};

/// `/api/models` and friends.
class TAOSQT_EXPORT Models
{
public:
    explicit Models(Client *c) : m_c(c) {}
    Reply *catalog() const;
    Reply *workers() const;
    Reply *providers() const;
    Reply *downloads() const;
    Reply *startDownload(const QString &appId, const QString &variantId) const;
    Reply *downloadProgress(const QString &downloadId) const;
    Reply *deleteModel(const QString &modelId) const;
private:
    Client *m_c;
};

} // namespace Taos
