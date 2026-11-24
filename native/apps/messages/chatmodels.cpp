// SPDX-License-Identifier: AGPL-3.0-or-later
#include "chatmodels.h"

#include <QJsonValue>
#include <algorithm>

#include "chatlogic.h"

using namespace Qt::StringLiterals;

namespace Taos::Messages {

// ---- channels -------------------------------------------------------------------------------

QHash<int, QByteArray> ChannelModel::roleNames() const
{
    return {{IdRole, "channelId"}, {NameRole, "name"}, {TypeRole, "type"}, {SectionRole, "section"},
            {PresenceRole, "presence"}, {PreviewRole, "preview"}, {PreviewAuthorRole, "previewAuthor"},
            {EmojiRole, "emoji"}, {LastAtRole, "lastAt"}, {UnreadRole, "unread"}, {IsHeaderRole, "isHeader"},
            {CollapsedRole, "collapsed"}, {CountRole, "count"}, {MutedRole, "muted"}};
}

QVariant ChannelModel::data(const QModelIndex &i, int role) const
{
    if (!i.isValid() || i.row() >= m_rows.size())
        return {};
    const ChannelRow &r = m_rows[i.row()];
    switch (role) {
    case IdRole: return r.id;
    case NameRole: return r.name;
    case TypeRole: return r.type;
    case SectionRole: return r.section;
    case PresenceRole: return r.presence;
    case PreviewRole: return r.preview;
    case PreviewAuthorRole: return r.previewAuthor;
    case EmojiRole: return r.emoji;
    case LastAtRole: return r.lastAt;
    case UnreadRole: return r.unread;
    case IsHeaderRole: return r.header;
    case CollapsedRole: return r.collapsed;
    case CountRole: return r.count;
    case MutedRole: return r.muted;
    }
    return {};
}

void ChannelModel::setRows(QList<ChannelRow> rows)
{
    beginResetModel();
    m_rows = std::move(rows);
    endResetModel();
}

int ChannelModel::indexOfChannel(const QString &id) const
{
    for (int i = 0; i < m_rows.size(); ++i)
        if (!m_rows[i].header && m_rows[i].id == id)
            return i;
    return -1;
}

// ---- messages -------------------------------------------------------------------------------

QHash<int, QByteArray> MessageModel::roleNames() const
{
    return {{IdRole, "msgId"}, {AuthorIdRole, "authorId"}, {AuthorNameRole, "authorName"},
            {AuthorTypeRole, "authorType"}, {OwnRole, "own"}, {ContentRole, "content"}, {PartsRole, "parts"},
            {StateRole, "state"}, {ErrorRole, "errorText"}, {TimeTextRole, "timeText"}, {FullTimeRole, "fullTime"},
            {GroupStartRole, "groupStart"}, {DayLabelRole, "dayLabel"}, {DividerRole, "newDivider"},
            {DeletedRole, "deleted"}, {EditedRole, "edited"}, {LongRole, "isLong"}, {ExpandedRole, "expanded"},
            {ReactionsTextRole, "reactionsText"}, {ReplyCountRole, "replyCount"}, {CreatedAtRole, "createdAt"},
            {LocalRole, "isLocal"}};
}

bool MessageModel::startsRun(int row) const
{
    if (row <= 0)
        return true;
    const Msg &a = m_msgs[row - 1];
    const Msg &b = m_msgs[row];
    return breaksRun(a.authorId, a.authorType, a.createdAt, b.authorId, b.authorType, b.createdAt);
}

QVariant MessageModel::data(const QModelIndex &i, int role) const
{
    if (!i.isValid() || i.row() >= m_msgs.size())
        return {};
    const int row = i.row();
    const Msg &m = m_msgs[row];
    switch (role) {
    case IdRole: return m.localId.isEmpty() ? m.id : m.localId;
    case AuthorIdRole: return m.authorId;
    case AuthorNameRole:
        if (m.own) return u"You"_s;
        if (m.authorType == u"system"_s) return u"System"_s;
        return m_names.value(m.authorId, m.authorId);
    case AuthorTypeRole: return m.authorType;
    case OwnRole: return m.own;
    case ContentRole: return m.content;
    case PartsRole: return m.parts;
    case StateRole: return m.state;
    case ErrorRole: return m.error;
    case TimeTextRole: return clockTime(m.createdAt);
    case FullTimeRole: return fullTime(m.createdAt);
    case GroupStartRole: return startsRun(row);
    case DayLabelRole:
        if (row == 0 || QDateTime::fromMSecsSinceEpoch(toMs(m_msgs[row - 1].createdAt)).date()
                != QDateTime::fromMSecsSinceEpoch(toMs(m.createdAt)).date())
            return dayLabel(m.createdAt);
        return QString();
    case DividerRole: return !m_dividerId.isEmpty() && m.id == m_dividerId;
    case DeletedRole: return m.deleted;
    case EditedRole: return m.editedAt > 0;
    case LongRole: return m.isLong;
    case ExpandedRole: return m.expanded;
    case ReactionsTextRole: {
        if (!m.reactions.isObject())
            return QString();
        QStringList out;
        const QJsonObject o = m.reactions.toObject();
        for (auto it = o.begin(); it != o.end(); ++it)
            if (it.value().toArray().size() > 0)
                out << it.key() + u" "_s + QString::number(it.value().toArray().size());
        return out.join(u"   "_s);
    }
    case ReplyCountRole: return m.replyCount;
    case CreatedAtRole: return m.createdAt;
    case LocalRole: return !m.localId.isEmpty();
    }
    return {};
}

void MessageModel::clear()
{
    beginResetModel();
    m_msgs.clear();
    m_dividerId.clear();
    endResetModel();
}

static void sortMsgs(QList<Msg> &l)
{
    std::stable_sort(l.begin(), l.end(), [](const Msg &a, const Msg &b) { return a.createdAt < b.createdAt; });
}

void MessageModel::setAll(QList<Msg> msgs, int unreadAtOpen)
{
    sortMsgs(msgs);
    beginResetModel();
    m_msgs = std::move(msgs);
    m_dividerId.clear();
    if (unreadAtOpen > 0 && unreadAtOpen < m_msgs.size())
        m_dividerId = m_msgs[m_msgs.size() - unreadAtOpen].id;
    endResetModel();
}

void MessageModel::prepend(QList<Msg> older)
{
    QList<Msg> fresh;
    for (Msg &m : older)
        if (!has(m.id))
            fresh << m;
    if (fresh.isEmpty())
        return;
    sortMsgs(fresh);
    beginInsertRows({}, 0, int(fresh.size()) - 1);
    for (int k = int(fresh.size()) - 1; k >= 0; --k)
        m_msgs.prepend(fresh[k]);
    endInsertRows();
    refreshAround(int(fresh.size()));
}

int MessageModel::add(const Msg &m)
{
    if (!m.id.isEmpty() && has(m.id))
        return -1;
    int row = int(m_msgs.size());
    while (row > 0 && m_msgs[row - 1].createdAt > m.createdAt && m_msgs[row - 1].localId.isEmpty())
        --row;
    beginInsertRows({}, row, row);
    m_msgs.insert(row, m);
    endInsertRows();
    refreshAround(row + 1);
    return row;
}

int MessageModel::indexOfId(const QString &id) const
{
    if (id.isEmpty())
        return -1;
    for (int i = int(m_msgs.size()) - 1; i >= 0; --i)
        if (m_msgs[i].id == id)
            return i;
    return -1;
}

int MessageModel::indexOfLocal(const QString &localId) const
{
    for (int i = int(m_msgs.size()) - 1; i >= 0; --i)
        if (m_msgs[i].localId == localId)
            return i;
    return -1;
}

void MessageModel::removeAt(int row)
{
    if (row < 0 || row >= m_msgs.size())
        return;
    beginRemoveRows({}, row, row);
    m_msgs.removeAt(row);
    endRemoveRows();
    refreshAround(row);
}

void MessageModel::replaceLocal(const QString &localId, const Msg &m)
{
    const int row = indexOfLocal(localId);
    if (row < 0)
        return;
    if (has(m.id)) { // the broadcast came first
        removeAt(row);
        return;
    }
    m_msgs[row] = m;
    emit dataChanged(index(row), index(row));
    refreshAround(row + 1);
}

void MessageModel::setFailed(const QString &localId, const QString &error)
{
    const int row = indexOfLocal(localId);
    if (row < 0)
        return;
    m_msgs[row].state = u"failed"_s;
    m_msgs[row].error = error;
    emit dataChanged(index(row), index(row), {StateRole, ErrorRole});
}

void MessageModel::setSending(const QString &localId)
{
    const int row = indexOfLocal(localId);
    if (row < 0)
        return;
    m_msgs[row].state = u"sending"_s;
    m_msgs[row].error.clear();
    emit dataChanged(index(row), index(row), {StateRole, ErrorRole});
}

void MessageModel::appendDelta(const QString &id, const QString &delta)
{
    const int row = indexOfId(id);
    if (row < 0)
        return;
    Msg &m = m_msgs[row];
    m.content += delta;
    m.state = u"streaming"_s;
    m.parts = partsFor(m.content, m.blocks);
    m.isLong = isLongText(m.content);
    emit dataChanged(index(row), index(row), {ContentRole, PartsRole, StateRole, LongRole});
}

void MessageModel::setState(const QString &id, const QString &state)
{
    const int row = indexOfId(id);
    if (row < 0)
        return;
    m_msgs[row].state = state;
    emit dataChanged(index(row), index(row), {StateRole});
}

void MessageModel::applyEdit(const QString &id, const QJsonObject &f)
{
    const int row = indexOfId(id);
    if (row < 0)
        return;
    Msg &m = m_msgs[row];
    if (f.contains(u"content"_s) && f.value(u"content"_s).isString())
        m.content = f.value(u"content"_s).toString();
    if (f.contains(u"content_blocks"_s) && f.value(u"content_blocks"_s).isArray())
        m.blocks = f.value(u"content_blocks"_s).toArray();
    if (f.value(u"edited_at"_s).isDouble())
        m.editedAt = f.value(u"edited_at"_s).toDouble();
    m.parts = partsFor(m.content, m.blocks);
    m.isLong = isLongText(m.content);
    emit dataChanged(index(row), index(row));
}

void MessageModel::markDeleted(const QString &id, double at)
{
    const int row = indexOfId(id);
    if (row < 0)
        return;
    m_msgs[row].deleted = true;
    Q_UNUSED(at)
    emit dataChanged(index(row), index(row), {DeletedRole});
}

void MessageModel::setReactions(const QString &id, const QJsonValue &r)
{
    const int row = indexOfId(id);
    if (row < 0)
        return;
    m_msgs[row].reactions = r;
    emit dataChanged(index(row), index(row), {ReactionsTextRole});
}

void MessageModel::setDisplayNames(QHash<QString, QString> names)
{
    m_names = std::move(names);
    if (!m_msgs.isEmpty())
        emit dataChanged(index(0), index(int(m_msgs.size()) - 1), {AuthorNameRole});
}

void MessageModel::toggleExpanded(const QString &id)
{
    for (int i = 0; i < m_msgs.size(); ++i)
        if (m_msgs[i].id == id || m_msgs[i].localId == id) {
            m_msgs[i].expanded = !m_msgs[i].expanded;
            emit dataChanged(index(i), index(i), {ExpandedRole});
            return;
        }
}

double MessageModel::oldestServerTime() const
{
    for (const Msg &m : m_msgs)
        if (m.localId.isEmpty())
            return m.createdAt;
    return 0;
}

void MessageModel::refreshAll()
{
    if (!m_msgs.isEmpty())
        emit dataChanged(index(0), index(int(m_msgs.size()) - 1), {GroupStartRole, DayLabelRole, DividerRole});
}

void MessageModel::refreshAround(int row)
{
    if (m_msgs.isEmpty())
        return;
    const int a = std::clamp(row - 1, 0, int(m_msgs.size()) - 1);
    const int b = std::clamp(row + 1, 0, int(m_msgs.size()) - 1);
    emit dataChanged(index(a), index(b), {GroupStartRole, DayLabelRole});
}

QVariantList MessageModel::partsFor(const QString &content, const QJsonArray &blocks)
{
    if (blocks.isEmpty())
        return segments(content);
    QVariantList out;
    for (const QJsonValue &bv : blocks) {
        const QJsonObject b = bv.toObject();
        const QString kind = b.value(u"kind"_s).toString();
        if (kind == u"text"_s) {
            out += segments(b.value(u"text"_s).toString());
        } else if (kind == u"thinking"_s) {
            out << QVariantMap{{u"kind"_s, u"thinking"_s},
                               {u"html"_s, markdownToHtml(b.value(u"text"_s).toString())},
                               {u"open"_s, b.value(u"collapsed"_s).isBool() && !b.value(u"collapsed"_s).toBool()}};
        } else if (kind == u"tool_call"_s) {
            out << QVariantMap{{u"kind"_s, u"tool"_s}, {u"name"_s, b.value(u"name"_s).toString()},
                               {u"status"_s, b.value(u"status"_s).toString()},
                               {u"input"_s, b.value(u"input_preview"_s).toString()},
                               {u"result"_s, b.value(u"result_preview"_s).toString()}};
        } else if (kind == u"status"_s || kind == u"question"_s) {
            QStringList opts;
            for (const QJsonValue &o : b.value(u"options"_s).toArray())
                opts << o.toString();
            out << QVariantMap{{u"kind"_s, u"status"_s}, {u"text"_s, b.value(u"text"_s).toString()},
                               {u"question"_s, kind == u"question"_s}, {u"options"_s, opts}};
        } else if (kind == u"decision"_s) {
            out << QVariantMap{{u"kind"_s, u"decision"_s}, {u"id"_s, b.value(u"decision_id"_s).toString()}};
        } else {
            out << QVariantMap{{u"kind"_s, u"unknown"_s}, {u"name"_s, kind}};
        }
    }
    return out;
}

Msg MessageModel::fromJson(const QJsonObject &o, const QString &currentUser)
{
    Msg m;
    m.id = o.value(u"id"_s).toString();
    m.channelId = o.value(u"channel_id"_s).toString();
    m.authorId = o.value(u"author_id"_s).toString();
    m.authorType = o.value(u"author_type"_s).toString();
    m.content = o.value(u"content"_s).toString();
    m.state = o.value(u"state"_s).toString(u"complete"_s);
    m.createdAt = o.value(u"created_at"_s).toDouble();
    m.editedAt = o.value(u"edited_at"_s).toDouble();
    m.deleted = o.value(u"deleted_at"_s).isDouble() && o.value(u"deleted_at"_s).toDouble() > 0;
    m.replyCount = o.value(u"reply_count"_s).toInt();
    m.blocks = o.value(u"content_blocks"_s).toArray();
    m.reactions = o.value(u"reactions"_s);
    m.attachments = o.value(u"attachments"_s);
    m.threadId = o.value(u"thread_id"_s).toString();
    m.own = m.authorType == u"user"_s && (m.authorId == currentUser || m.authorId == u"user"_s);
    m.parts = partsFor(m.content, m.blocks);
    m.isLong = isLongText(m.content);
    return m;
}

} // namespace Taos::Messages
