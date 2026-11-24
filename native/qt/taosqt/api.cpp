#include "taosqt/api.h"

using namespace Qt::StringLiterals;

namespace Taos {

Reply *Notifications::list(bool unreadOnly) const
{
    return m_c->call(u"notifications.list"_s, {{u"unread_only"_s, unreadOnly}});
}
Reply *Notifications::markRead(qint64 id) const
{
    return m_c->call(u"notifications.mark_read"_s, {{u"id"_s, id}});
}
Reply *Notifications::markAllRead() const { return m_c->call(u"notifications.mark_all_read"_s); }
Reply *Notifications::archive(qint64 id) const
{
    return m_c->call(u"notifications.archive"_s, {{u"id"_s, id}});
}
Reply *Notifications::clear() const { return m_c->call(u"notifications.clear"_s); }

Reply *Decisions::list(const QString &status, const QString &projectId, int limit) const
{
    QJsonObject a;
    if (!status.isEmpty())
        a.insert(u"status"_s, status);
    if (!projectId.isEmpty())
        a.insert(u"project_id"_s, projectId);
    if (limit > 0)
        a.insert(u"limit"_s, limit);
    return m_c->call(u"decisions.list"_s, a);
}
Reply *Decisions::get(const QString &id) const { return m_c->call(u"decisions.get"_s, {{u"id"_s, id}}); }
Reply *Decisions::history(const QString &id) const { return m_c->call(u"decisions.history"_s, {{u"id"_s, id}}); }
Reply *Decisions::answer(const QString &id, const QJsonValue &value, const QString &other, const QString &note) const
{
    QJsonObject a{{u"id"_s, id}};
    if (!value.isNull() && !value.isUndefined())
        a.insert(u"value"_s, value);
    if (!other.isEmpty())
        a.insert(u"other"_s, other);
    if (!note.isEmpty())
        a.insert(u"note"_s, note);
    return m_c->call(u"decisions.answer"_s, a);
}
Reply *Decisions::addNote(const QString &id, const QString &text) const
{
    return m_c->call(u"decisions.add_note"_s, {{u"id"_s, id}, {u"text"_s, text}});
}

Reply *Dashboard::snapshot() const { return m_c->call(u"dashboard.snapshot"_s); }
Reply *Dashboard::activity() const { return m_c->call(u"dashboard.activity"_s); }
Reply *Dashboard::loadedModels() const { return m_c->call(u"dashboard.loaded_models"_s); }
Reply *Dashboard::schedulerStats() const { return m_c->call(u"dashboard.scheduler_stats"_s); }
Reply *Dashboard::schedulerTasks(int limit) const
{
    return m_c->call(u"dashboard.scheduler_tasks"_s, {{u"limit"_s, limit}});
}
Reply *Dashboard::workers() const { return m_c->call(u"dashboard.workers"_s); }

Reply *Models::catalog() const { return m_c->call(u"models.catalog"_s); }
Reply *Models::workers() const { return m_c->call(u"models.workers"_s); }
Reply *Models::providers() const { return m_c->call(u"models.providers"_s); }
Reply *Models::downloads() const { return m_c->call(u"models.downloads"_s); }
Reply *Models::startDownload(const QString &appId, const QString &variantId) const
{
    return m_c->call(u"models.start_download"_s, {{u"app_id"_s, appId}, {u"variant_id"_s, variantId}});
}
Reply *Models::downloadProgress(const QString &downloadId) const
{
    return m_c->call(u"models.download_progress"_s, {{u"download_id"_s, downloadId}});
}
Reply *Models::deleteModel(const QString &modelId) const
{
    return m_c->call(u"models.delete"_s, {{u"model_id"_s, modelId}});
}

} // namespace Taos
