// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The two list models of the Messages app: the channel list (section headers and
// channel rows in one flat list, so a ListView can collapse sections) and the
// conversation (ascending, with run grouping, day separators and the optimistic
// rows of messages being sent).
#include <QAbstractListModel>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QVariantList>

namespace Taos::Messages {

struct ChannelRow
{
    bool header = false;
    QString id;
    QString name;        // display name
    QString type;
    QString section;
    QString presence;    // live, working, idle, or empty
    QString preview;
    QString previewAuthor;
    QString emoji;
    double lastAt = 0;
    int unread = 0;
    bool collapsed = false;
    bool muted = false;
    int count = 0;       // headers: channels in the section
};

class ChannelModel : public QAbstractListModel
{
    Q_OBJECT
public:
    enum Role {
        IdRole = Qt::UserRole + 1, NameRole, TypeRole, SectionRole, PresenceRole, PreviewRole,
        PreviewAuthorRole, EmojiRole, LastAtRole, UnreadRole, IsHeaderRole, CollapsedRole, CountRole, MutedRole
    };
    explicit ChannelModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setRows(QList<ChannelRow> rows);
    const QList<ChannelRow> &rows() const { return m_rows; }
    int indexOfChannel(const QString &id) const;

private:
    QList<ChannelRow> m_rows;
};

struct Msg
{
    QString id;
    QString localId;     // set while the row is an optimistic one
    QString channelId;
    QString authorId;
    QString authorType;
    QString content;
    QString state;       // complete, pending, streaming, error, sending, failed
    QString error;
    double createdAt = 0;
    double editedAt = 0;
    bool deleted = false;
    bool own = false;
    bool expanded = false;
    int replyCount = 0;
    QJsonArray blocks;
    QJsonValue reactions;
    QJsonValue attachments;
    QVariantList parts;
    bool isLong = false;
    QString threadId;
};

class MessageModel : public QAbstractListModel
{
    Q_OBJECT
public:
    enum Role {
        IdRole = Qt::UserRole + 1, AuthorIdRole, AuthorNameRole, AuthorTypeRole, OwnRole, ContentRole, PartsRole,
        StateRole, ErrorRole, TimeTextRole, FullTimeRole, GroupStartRole, DayLabelRole, DividerRole, DeletedRole,
        EditedRole, LongRole, ExpandedRole, ReactionsTextRole, ReplyCountRole, CreatedAtRole, LocalRole
    };
    explicit MessageModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_msgs.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    void clear();
    void setAll(QList<Msg> msgs, int unreadAtOpen);
    void prepend(QList<Msg> older);
    /// Insert (by time) or, when the id exists, ignore. Returns the row, or -1 when it existed.
    int add(const Msg &m);
    bool has(const QString &id) const { return indexOfId(id) >= 0; }
    int indexOfId(const QString &id) const;
    int indexOfLocal(const QString &localId) const;
    void removeAt(int row);
    /// Replace the optimistic row by the stored message (keeps its place unless the time order says otherwise).
    void replaceLocal(const QString &localId, const Msg &m);
    void setFailed(const QString &localId, const QString &error);
    void setSending(const QString &localId);
    void appendDelta(const QString &id, const QString &delta);
    void setState(const QString &id, const QString &state);
    void applyEdit(const QString &id, const QJsonObject &frame);
    void markDeleted(const QString &id, double at);
    void setReactions(const QString &id, const QJsonValue &r);
    void setDisplayNames(QHash<QString, QString> names);
    void setDividerAt(const QString &id) { m_dividerId = id; refreshAll(); }
    Q_INVOKABLE void toggleExpanded(const QString &id);
    const QList<Msg> &msgs() const { return m_msgs; }
    double oldestServerTime() const;
    int count() const { return int(m_msgs.size()); }

    static QVariantList partsFor(const QString &content, const QJsonArray &blocks);
    static Msg fromJson(const QJsonObject &o, const QString &currentUser);

private:
    bool startsRun(int row) const;
    void refreshAll();
    void refreshAround(int row);
    QList<Msg> m_msgs;
    QHash<QString, QString> m_names;
    QString m_dividerId;
};

} // namespace Taos::Messages
