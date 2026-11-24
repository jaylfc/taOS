#include "chatengine.h"

#include <QClipboard>
#include <QDateTime>
#include <QDesktopServices>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QUrl>
#include <QtQml/qqml.h>
#include <algorithm>

using namespace Qt::StringLiterals;

namespace Taos::Messages {

namespace {
qint64 nowMs() { return QDateTime::currentMSecsSinceEpoch(); }
QString phaseText(const QString &phase, const QString &detail)
{
    QString t = u"thinking"_s;
    if (phase == u"tool"_s) t = u"using a tool"_s;
    else if (phase == u"reading"_s) t = u"reading"_s;
    else if (phase == u"writing"_s) t = u"writing"_s;
    else if (phase == u"searching"_s) t = u"searching"_s;
    else if (phase == u"planning"_s) t = u"planning"_s;
    if (!detail.isEmpty())
        t += u": "_s + detail;
    return t;
}
} // namespace

ChatEngine::ChatEngine(QObject *parent) : QObject(parent)
{
    m_clock.setInterval(1000);
    connect(&m_clock, &QTimer::timeout, this, &ChatEngine::tickTimers);
    m_poll.setInterval(20000);
    connect(&m_poll, &QTimer::timeout, this, [this] {
        if (m_running) {
            fetchUnread();
            fetchChannels();
        }
    });
}

ChatEngine::~ChatEngine() = default;

void ChatEngine::registerTypes()
{
    qmlRegisterType<ChatEngine>("Taos.Messages", 1, 0, "ChatEngine");
    qmlRegisterUncreatableType<ChannelModel>("Taos.Messages", 1, 0, "ChannelModel", u"owned by ChatEngine"_s);
    qmlRegisterUncreatableType<MessageModel>("Taos.Messages", 1, 0, "MessageModel", u"owned by ChatEngine"_s);
}

Taos::Client *ChatEngine::client() const
{
    return m_client ? m_client.data() : Taos::defaultClient();
}

void ChatEngine::setRunning(bool on)
{
    if (on == m_running)
        return;
    m_running = on;
    Q_EMIT runningChanged();
    on ? start() : stop();
}

void ChatEngine::setWindowActive(bool on)
{
    if (on == m_active)
        return;
    m_active = on;
    Q_EMIT windowActiveChanged();
    if (on && m_running) {
        fetchUnread();
        markRead();
    }
}

void ChatEngine::start()
{
    m_clock.start();
    m_poll.start();
    fetchUser();
    fetchChannels();
    fetchUnread();
    fetchAgents();
    openStream();
}

void ChatEngine::stop()
{
    m_clock.stop();
    m_poll.stop();
    delete m_stream.data();
    m_stream.clear();
    if (m_live) {
        m_live = false;
        Q_EMIT liveChanged();
    }
}

void ChatEngine::fetchUser()
{
    client()->call(u"auth.me"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        const QString id = r.value.toObject().value(u"id"_s).toString();
        if (id != m_userId) {
            m_userId = id;
            Q_EMIT userChanged();
        }
    });
}

void ChatEngine::fetchChannels()
{
    if (!m_channelsLoaded) {
        m_channelsLoading = true;
        Q_EMIT channelsChanged();
    }
    client()->call(u"chat.channels"_s)->then(this, [this](const Taos::Result &r) {
        m_channelsLoading = false;
        if (!r.ok()) {
            m_channelsError = r.error.userMessage();
            Q_EMIT channelsChanged();
            return;
        }
        m_channelsError.clear();
        m_channels = r.value.toArray();
        m_channelsLoaded = true;
        rebuildChannels();
        applyPendingOpen();
    });
}

void ChatEngine::fetchUnread()
{
    client()->call(u"chat.unread"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok()) {
            if (m_awaitUnread) {
                m_awaitUnread = false;
                fetchMessages();
            }
            return;
        }
        QHash<QString, int> u;
        const QJsonObject o = r.value.toObject();
        for (auto it = o.begin(); it != o.end(); ++it)
            u.insert(it.key(), it.value().toInt());
        m_unreadLoaded = true;
        // a conversation opened before the counts were known: they place its "New" divider
        if (m_awaitUnread) {
            m_awaitUnread = false;
            m_unreadAtOpen = u.value(m_selected);
            fetchMessages();
        }
        // the open conversation is being read: it stays at zero
        if (!m_selected.isEmpty() && m_active)
            u.remove(m_selected);
        m_unread = u;
        rebuildChannels();
    });
}

void ChatEngine::fetchAgents()
{
    client()->call(u"chat.agents"_s, {{u"include_guests"_s, true}})->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        m_agents.clear();
        for (const QJsonValue &v : r.value.toArray()) {
            const QJsonObject o = v.toObject();
            m_agents.append({o.value(u"name"_s).toString(), o.value(u"display_name"_s).toString(),
                             o.value(u"status"_s).toString(), o.value(u"emoji"_s).toString()});
        }
        m_agentsLoaded = true;
        QHash<QString, QString> names;
        for (const AgentInfo &a : std::as_const(m_agents))
            names.insert(a.name, a.displayName.isEmpty() ? a.name : a.displayName);
        m_messageModel.setDisplayNames(names);
        rebuildChannels();
    });
    client()->call(u"chat.archived_agents"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        m_archivedKeys.clear();
        for (const QJsonValue &v : r.value.toArray()) {
            const QJsonObject o = v.toObject();
            m_archivedKeys.insert(o.value(u"archived_slug"_s).toString());
            const QJsonObject orig = o.value(u"original"_s).toObject();
            m_archivedKeys.insert(orig.value(u"name"_s).toString());
            m_archivedKeys.insert(orig.value(u"display_name"_s).toString());
        }
        m_archivedKeys.remove(QString());
        m_archivedLoaded = true;
        rebuildChannels();
    });
}

void ChatEngine::openStream()
{
    if (m_stream || !client())
        return;
    m_stream = client()->openStream(u"/ws/chat"_s, {}, this);
    connect(m_stream, &Taos::EventStream::eventReceived, this,
            [this](const QString &event, const QString &, const QJsonValue &data) {
                if (event == u"chat"_s && data.isObject())
                    onFrame(data.toObject());
            });
    connect(m_stream, &Taos::EventStream::connected, this, [this] {
        // Frames sent while the socket was down are gone: ask again.
        joinSelected();
        fetchChannels();
        fetchUnread();
        if (!m_selected.isEmpty() && m_messageModel.count() > 0)
            reloadMessages();
    });
    connect(m_stream, &Taos::EventStream::stateChanged, this, [this] {
        const bool live = m_stream && m_stream->state() == Taos::EventStream::Live;
        if (live != m_live) {
            m_live = live;
            Q_EMIT liveChanged();
        }
    });
    m_stream->start();
}

void ChatEngine::joinSelected()
{
    QJsonArray ids;
    if (!m_selected.isEmpty())
        ids.append(m_selected);
    client()->call(u"chat.join"_s, {{u"channel_ids"_s, ids}});
}

QJsonObject ChatEngine::channelJson(const QString &id) const
{
    for (const QJsonValue &v : m_channels)
        if (v.toObject().value(u"id"_s).toString() == id)
            return v.toObject();
    return {};
}

QString ChatEngine::agentDisplayName(const QString &slug) const
{
    for (const AgentInfo &a : m_agents)
        if (a.name == slug)
            return a.displayName.isEmpty() ? a.name : a.displayName;
    return slug;
}

QString ChatEngine::selectedName() const
{
    const QJsonObject c = channelJson(m_selected);
    if (c.isEmpty())
        return QString();
    const QString n = c.value(u"name"_s).toString();
    return c.value(u"type"_s).toString() == u"dm"_s ? agentDisplayName(n) : n;
}
QString ChatEngine::selectedType() const { return channelJson(m_selected).value(u"type"_s).toString(); }
QString ChatEngine::selectedTopic() const
{
    const QJsonObject c = channelJson(m_selected);
    const QString t = c.value(u"topic"_s).toString();
    return t.isEmpty() ? c.value(u"description"_s).toString() : t;
}
QStringList ChatEngine::selectedMembers() const
{
    QStringList out;
    for (const QJsonValue &v : channelJson(m_selected).value(u"members"_s).toArray())
        out << v.toString();
    return out;
}

int ChatEngine::totalUnread() const
{
    int n = 0;
    for (auto it = m_unread.begin(); it != m_unread.end(); ++it)
        n += it.value();
    return n;
}

void ChatEngine::rebuildChannels()
{
    struct Row { ChannelRow r; };
    QList<ChannelRow> chans;
    const bool listsLoaded = !m_agents.isEmpty() || !m_archivedKeys.isEmpty();
    for (const QJsonValue &v : std::as_const(m_channels)) {
        const QJsonObject c = v.toObject();
        const QJsonObject settings = c.value(u"settings"_s).toObject();
        if (settings.value(u"archived"_s).toBool())
            continue;
        QStringList members;
        for (const QJsonValue &m : c.value(u"members"_s).toArray())
            members << m.toString();
        ChannelRow r;
        r.id = c.value(u"id"_s).toString();
        r.type = c.value(u"type"_s).toString();
        const QString name = c.value(u"name"_s).toString();
        const Bucket b = bucketFor(name, members, settings.value(u"kind"_s).toString(), m_agents, m_archivedKeys,
                                   listsLoaded);
        r.section = sectionFor(r.type, b);
        r.name = r.type == u"dm"_s && b != Bucket::NonAgent ? agentDisplayName(name) : name;
        // presence of agent DMs and of channels bound to an agent
        const QString bound = settings.value(u"taostalk_agent"_s).toString();
        QString agent = bound;
        if (agent.isEmpty() && r.type == u"dm"_s)
            for (const QString &m : members)
                if (m != u"user"_s) { agent = m; break; }
        if (b == Bucket::Live || b == Bucket::Suspended || b == Bucket::Archived)
            r.presence = presenceFor(b == Bucket::Live ? u"running"_s : QString(),
                                     !agent.isEmpty() && m_working.contains(agent));
        else if (!bound.isEmpty() && m_working.contains(bound))
            r.presence = u"working"_s;
        for (const AgentInfo &a : m_agents)
            if (a.name == agent && !a.emoji.isEmpty()) r.emoji = a.emoji;
        r.preview = c.value(u"last_preview"_s).toString().simplified().left(120);
        r.previewAuthor = c.value(u"last_preview_author"_s).toString();
        r.lastAt = c.value(u"last_message_at"_s).toDouble();
        r.unread = r.id == m_selected && m_active ? 0 : m_unread.value(r.id);
        r.muted = settings.value(u"muted"_s).toArray().contains(u"user"_s);
        chans.append(r);
    }
    std::stable_sort(chans.begin(), chans.end(), [](const ChannelRow &a, const ChannelRow &b) {
        const int sa = sectionOrder(a.section), sb = sectionOrder(b.section);
        if (sa != sb)
            return sa < sb;
        if (a.lastAt != b.lastAt)
            return a.lastAt > b.lastAt;
        return a.name.localeAwareCompare(b.name) < 0;
    });
    QList<ChannelRow> rows;
    QString cur;
    int headerAt = -1;
    for (const ChannelRow &c : std::as_const(chans)) {
        if (c.section != cur) {
            cur = c.section;
            ChannelRow h;
            h.header = true;
            h.section = h.name = cur;
            h.collapsed = m_collapsed.contains(cur);
            rows.append(h);
            headerAt = int(rows.size()) - 1;
        }
        rows[headerAt].count++;
        rows[headerAt].unread += c.unread;
        // collapsed: only unread or the open channel stay (index.tsx:1734)
        if (m_collapsed.contains(cur) && c.unread == 0 && c.id != m_selected)
            continue;
        rows.append(c);
    }
    if (!cur.isEmpty() || m_channels.isEmpty()) {
        // "Channels" is always listed in the web app, even when empty (index.tsx:2265)
    }
    m_channelModel.setRows(rows);
    Q_EMIT channelsChanged();
}

void ChatEngine::toggleSection(const QString &section)
{
    if (!m_collapsed.remove(section))
        m_collapsed.insert(section);
    rebuildChannels();
}

void ChatEngine::applyPendingOpen()
{
    if (!m_pendingChannel.isEmpty()) {
        const QString id = m_pendingChannel;
        m_pendingChannel.clear();
        openChannel(id); // not in the list: the controller decides what it answers
    }
    if (!m_pendingAgent.isEmpty()) {
        const QString a = m_pendingAgent;
        m_pendingAgent.clear();
        openAgent(a);
    }
}

void ChatEngine::openAgent(const QString &name)
{
    if (!m_channelsLoaded) {
        m_pendingAgent = name;
        return;
    }
    // findAgentDm of first-chat.tsx: a DM whose name or other member is the agent.
    for (const QJsonValue &v : std::as_const(m_channels)) {
        const QJsonObject c = v.toObject();
        if (c.value(u"type"_s).toString() != u"dm"_s)
            continue;
        if (c.value(u"settings"_s).toObject().value(u"kind"_s).toString() == u"a2a"_s)
            continue;
        QStringList members;
        for (const QJsonValue &m : c.value(u"members"_s).toArray())
            members << m.toString();
        if (c.value(u"name"_s).toString() == name
            || (members.contains(name) && members.contains(u"user"_s) && members.size() == 2)) {
            openChannel(c.value(u"id"_s).toString());
            return;
        }
    }
    m_messagesError = u"No conversation with %1 yet."_s.arg(name);
    Q_EMIT messagesStateChanged();
}

void ChatEngine::applyArguments(const QStringList &args)
{
    for (int i = 0; i < args.size(); ++i) {
        const QString &a = args[i];
        auto val = [&](const QString &flag) -> QString {
            if (a == flag && i + 1 < args.size())
                return args[i + 1];
            if (a.startsWith(flag + u"="_s))
                return a.mid(flag.size() + 1);
            return QString();
        };
        if (const QString c = val(u"--channel"_s); !c.isEmpty()) {
            if (m_channelsLoaded)
                openChannel(c);
            else
                m_pendingChannel = c;
        } else if (const QString n = val(u"--agent"_s); !n.isEmpty()) {
            openAgent(n);
        }
    }
}

void ChatEngine::openChannel(const QString &id)
{
    if (id.isEmpty())
        return;
    if (id == m_selected && m_messageModel.count() > 0)
        return;
    if (!m_selected.isEmpty())
        m_drafts.insert(m_selected, m_drafts.value(m_selected)); // kept as set by the composer
    m_selected = id;
    ++m_openSeq;
    m_unreadAtOpen = m_unread.value(id);
    m_messageModel.clear();
    m_typing.clear();
    m_hasOlder = false;
    m_messagesError.clear();
    m_watchAgent.clear();
    updateTyping();
    updateStall();
    setSendError(QString());
    Q_EMIT selectedChanged();
    joinSelected();
    m_awaitUnread = !m_unreadLoaded;
    m_messagesLoading = true;
    if (m_awaitUnread)
        fetchUnread();
    else
        fetchMessages();
    rebuildChannels();
}

void ChatEngine::fetchMessages()
{
    const int seq = m_openSeq;
    const QString id = m_selected;
    m_messagesLoading = true;
    m_messagesError.clear();
    Q_EMIT messagesStateChanged();
    client()->call(u"chat.messages"_s, {{u"channel_id"_s, id}, {u"limit"_s, 50}})
        ->then(this, [this, seq, id](const Taos::Result &r) {
            if (seq != m_openSeq)
                return;
            m_messagesLoading = false;
            if (!r.ok()) {
                m_messagesError = r.error.userMessage();
                Q_EMIT messagesStateChanged();
                return;
            }
            const QJsonArray arr = r.value.toArray();
            QList<Msg> msgs;
            for (const QJsonValue &v : arr)
                msgs.append(MessageModel::fromJson(v.toObject(), m_userId));
            // keep optimistic rows that are still in flight
            QList<Msg> locals;
            for (const Msg &m : m_messageModel.msgs())
                if (!m.localId.isEmpty())
                    locals.append(m);
            m_messageModel.setAll(msgs, m_unreadAtOpen);
            for (const Msg &l : std::as_const(locals))
                m_messageModel.add(l);
            m_unreadAtOpen = 0;
            m_hasOlder = arr.size() >= 50;
            Q_EMIT messagesStateChanged();
            Q_EMIT conversationLoaded();
            markRead();
        });
}

void ChatEngine::reloadMessages()
{
    if (m_selected.isEmpty())
        return;
    const int seq = ++m_openSeq;
    Q_UNUSED(seq)
    m_unreadAtOpen = 0;
    fetchMessages();
}

void ChatEngine::loadOlder()
{
    if (m_loadingOlder || !m_hasOlder || m_selected.isEmpty())
        return;
    const double before = m_messageModel.oldestServerTime();
    if (before <= 0)
        return;
    m_loadingOlder = true;
    Q_EMIT messagesStateChanged();
    const int seq = m_openSeq;
    client()->call(u"chat.messages"_s, {{u"channel_id"_s, m_selected}, {u"limit"_s, 50}, {u"before"_s, before}})
        ->then(this, [this, seq](const Taos::Result &r) {
            if (seq != m_openSeq)
                return;
            m_loadingOlder = false;
            if (r.ok()) {
                const QJsonArray arr = r.value.toArray();
                QList<Msg> msgs;
                for (const QJsonValue &v : arr)
                    msgs.append(MessageModel::fromJson(v.toObject(), m_userId));
                m_hasOlder = arr.size() >= 50;
                const int before = m_messageModel.count();
                m_messageModel.prepend(msgs);
                Q_EMIT olderLoaded(m_messageModel.count() - before);
            }
            Q_EMIT messagesStateChanged();
        });
}

void ChatEngine::refresh()
{
    fetchChannels();
    fetchUnread();
    fetchAgents();
    if (!m_selected.isEmpty())
        reloadMessages();
}

void ChatEngine::markRead()
{
    if (m_selected.isEmpty() || !m_active)
        return;
    const bool had = m_unread.remove(m_selected) > 0;
    client()->call(u"chat.mark_read"_s, {{u"channel_id"_s, m_selected}});
    if (had)
        rebuildChannels();
}

void ChatEngine::setDraft(const QString &channelId, const QString &text)
{
    if (channelId.isEmpty())
        return;
    if (text.isEmpty())
        m_drafts.remove(channelId);
    else
        m_drafts.insert(channelId, text);
}

void ChatEngine::noteTyping()
{
    const qint64 t = nowMs();
    if (m_selected.isEmpty() || t - m_lastTypingSent < 3000)
        return;
    m_lastTypingSent = t;
    client()->call(u"chat.typing"_s, {{u"channel_id"_s, m_selected}, {u"author_id"_s, u"user"_s}});
}

void ChatEngine::setSendError(const QString &e)
{
    if (e == m_sendError)
        return;
    m_sendError = e;
    Q_EMIT sendErrorChanged();
}
void ChatEngine::clearSendError() { setSendError(QString()); }

void ChatEngine::send(const QString &text)
{
    const QString t = text.trimmed();
    if (t.isEmpty() || m_selected.isEmpty())
        return;
    const QString local = u"local-%1"_s.arg(++m_localSeq);
    Msg m;
    m.localId = local;
    m.channelId = m_selected;
    m.authorId = m_userId.isEmpty() ? u"user"_s : m_userId;
    m.authorType = u"user"_s;
    m.content = t;
    m.state = u"sending"_s;
    m.createdAt = double(nowMs()) / 1000.0;
    m.own = true;
    m.parts = MessageModel::partsFor(t, {});
    m.isLong = isLongText(t);
    m_messageModel.add(m);
    setSendError(QString());
    // Arm the stall watch: an agent DM, or an @mentioned agent (index.tsx:1794).
    QStringList names;
    for (const AgentInfo &a : std::as_const(m_agents))
        names << a.name;
    const QString agent = pickWatchAgent(selectedType(), selectedMembers(), t, names);
    if (!agent.isEmpty()) {
        m_watchAgent = agent;
        m_watchChannel = m_selected;
        m_watchStream.clear();
        m_watchActivity = nowMs();
    }
    Q_EMIT messageAppended(true);
    sendLocal(local, t);
}

void ChatEngine::sendLocal(const QString &localId, const QString &text)
{
    const int seq = m_openSeq;
    const QString channel = m_selected;
    client()->call(u"chat.send"_s, {{u"channel_id"_s, channel}, {u"content"_s, text}})
        ->then(this, [this, seq, localId](const Taos::Result &r) {
            if (seq != m_openSeq)
                return;
            if (!r.ok()) {
                m_messageModel.setFailed(localId, r.error.userMessage());
                setSendError(r.error.userMessage());
                return;
            }
            const QJsonObject o = r.value.toObject();
            if (o.contains(u"handled"_s)) { // /help: the server broadcasts its own message
                const int row = m_messageModel.indexOfLocal(localId);
                m_messageModel.removeAt(row);
                const QJsonObject sys = o.value(u"system_message"_s).toObject();
                if (!sys.isEmpty())
                    m_messageModel.add(MessageModel::fromJson(sys, m_userId));
                return;
            }
            m_messageModel.replaceLocal(localId, MessageModel::fromJson(o.value(u"message"_s).toObject(), m_userId));
            // the conversation list's preview
            fetchChannels();
        });
}

void ChatEngine::retry(const QString &localId)
{
    const int row = m_messageModel.indexOfLocal(localId);
    if (row < 0)
        return;
    const QString text = m_messageModel.msgs()[row].content;
    m_messageModel.setSending(localId);
    setSendError(QString());
    sendLocal(localId, text);
}

void ChatEngine::discard(const QString &localId)
{
    m_messageModel.removeAt(m_messageModel.indexOfLocal(localId));
    setSendError(QString());
}

void ChatEngine::onFrame(const QJsonObject &f)
{
    const QString type = f.value(u"type"_s).toString();
    if (type == u"message"_s) {
        const QString ch = f.value(u"channel_id"_s).toString();
        Msg m = MessageModel::fromJson(f, m_userId);
        if (ch != m_selected) {
            if (!m.own)
                m_unread[ch] += 1;
            // the list's preview line
            for (int i = 0; i < m_channels.size(); ++i) {
                QJsonObject c = m_channels[i].toObject();
                if (c.value(u"id"_s).toString() == ch) {
                    c.insert(u"last_preview"_s, m.content);
                    c.insert(u"last_preview_author"_s, m.authorId);
                    c.insert(u"last_message_at"_s, m.createdAt);
                    m_channels[i] = c;
                }
            }
            rebuildChannels();
            return;
        }
        // our own optimistic row for the same text: the POST result will merge it, drop duplicates by id
        const int row = m_messageModel.add(m);
        if (row >= 0) {
            Q_EMIT messageAppended(m.own);
            if (!m.own) {
                Q_EMIT incomingMessage(agentDisplayName(m.authorId), m.content.left(300));
                markRead();
            }
        }
        // the waited-on agent's reply is activity (index.tsx:1257)
        if (!m_watchAgent.isEmpty() && ch == m_watchChannel && m.authorId == m_watchAgent) {
            m_watchActivity = nowMs();
            m_watchStream = m.id;
            updateStall();
        }
        return;
    }
    if (type == u"message_delta"_s) {
        const QString id = f.value(u"message_id"_s).toString();
        m_messageModel.appendDelta(id, f.value(u"delta"_s).toString());
        if (!m_watchStream.isEmpty() && id == m_watchStream) {
            m_watchActivity = nowMs();
            updateStall();
        }
        return;
    }
    if (type == u"message_state"_s) {
        const QString id = f.value(u"message_id"_s).toString();
        const QString st = f.value(u"state"_s).toString();
        m_messageModel.setState(id, st);
        if ((st == u"complete"_s || st == u"error"_s) && (m_watchStream.isEmpty() || id == m_watchStream)) {
            m_watchAgent.clear();
            updateStall();
        }
        return;
    }
    if (type == u"typing"_s) {
        if (f.value(u"kind"_s).toString() == u"human"_s) {
            const QString slug = f.value(u"slug"_s).toString();
            if (!slug.isEmpty() && slug != m_userId && slug != u"user"_s)
                m_typing.insert(u"h:"_s + slug, {QString(), QString(), nowMs() + 3500, false});
        } else if (f.value(u"user_type"_s).toString() == u"agent"_s) {
            const QString slug = f.value(u"user_id"_s).toString();
            m_typing.insert(u"a:"_s + slug, {QString(), QString(), nowMs() + 5000, true});
        }
        updateTyping();
        return;
    }
    if (type == u"thinking"_s) {
        const QString slug = f.value(u"slug"_s).toString();
        if (slug == m_watchAgent) {
            m_watchActivity = nowMs();
            updateStall();
        }
        if (f.value(u"state"_s).toString() == u"start"_s) {
            m_typing.insert(u"a:"_s + slug, {f.value(u"phase"_s).toString(), f.value(u"detail"_s).toString(), 0, true});
            if (!m_working.contains(slug)) {
                m_working.insert(slug);
                rebuildChannels();
            }
        } else {
            m_typing.remove(u"a:"_s + slug);
            if (m_working.remove(slug))
                rebuildChannels();
        }
        updateTyping();
        return;
    }
    if (type == u"reaction_update"_s) {
        m_messageModel.setReactions(f.value(u"message_id"_s).toString(), f.value(u"reactions"_s));
    } else if (type == u"message_edit"_s) {
        m_messageModel.applyEdit(f.value(u"message_id"_s).toString(), f);
    } else if (type == u"message_delete"_s) {
        m_messageModel.markDeleted(f.value(u"message_id"_s).toString(), f.value(u"deleted_at"_s).toDouble());
    } else if (type == u"error"_s) {
        if (f.value(u"channel_id"_s).toString() == m_selected)
            setSendError(f.value(u"error"_s).toString());
    }
}

void ChatEngine::tickTimers()
{
    const qint64 t = nowMs();
    bool changed = false;
    for (auto it = m_typing.begin(); it != m_typing.end();) {
        if (it->until > 0 && it->until < t) {
            it = m_typing.erase(it);
            changed = true;
        } else {
            ++it;
        }
    }
    if (changed)
        updateTyping();
    if (!m_watchAgent.isEmpty())
        updateStall();
}

void ChatEngine::updateTyping()
{
    QStringList humans, agents;
    for (auto it = m_typing.begin(); it != m_typing.end(); ++it) {
        const QString slug = it.key().mid(2);
        if (it->agent)
            agents << u"@%1 is %2…"_s.arg(slug, it->phase.isEmpty() && it->detail.isEmpty() ? u"typing"_s : phaseText(it->phase, it->detail));
        else
            humans << slug;
    }
    humans.sort();
    agents.sort();
    QString text;
    if (humans.size() == 1) text = humans[0] + u" is typing…"_s;
    else if (humans.size() == 2) text = humans[0] + u" and "_s + humans[1] + u" are typing…"_s;
    else if (humans.size() > 2) text = u"%1 and %2 others are typing…"_s.arg(humans[0]).arg(humans.size() - 1);
    for (const QString &a : std::as_const(agents))
        text += (text.isEmpty() ? QString() : u"\n"_s) + a;
    if (text != m_typingText) {
        m_typingText = text;
        Q_EMIT typingChanged();
    }
}

void ChatEngine::updateStall()
{
    QString text;
    bool stalled = false;
    if (!m_watchAgent.isEmpty() && m_watchChannel == m_selected) {
        const qint64 el = nowMs() - m_watchActivity;
        if (el >= kStallHintMs) {
            stalled = el >= kStallWarnMs;
            text = stalled ? u"@%1 may be stalled (%2 s without a sign of life). Try again or check the agent."_s
                                 .arg(m_watchAgent).arg(el / 1000)
                           : u"@%1 is taking longer than usual (%2 s)."_s.arg(m_watchAgent).arg(el / 1000);
        }
    }
    if (text != m_stallText || stalled != m_stalled) {
        m_stallText = text;
        m_stalled = stalled;
        Q_EMIT stallChanged();
    }
}

QStringList ChatEngine::mentionCandidates(const QString &text, int cursor) const
{
    const MentionAt m = mentionAt(text, cursor);
    if (m.atIndex < 0)
        return {};
    return Taos::Messages::mentionCandidates(selectedMembers(), m.partial);
}

int ChatEngine::mentionAtIndex(const QString &text, int cursor) const
{
    return mentionAt(text, cursor).atIndex;
}

QString ChatEngine::insertMention(const QString &text, int cursor, const QString &slug) const
{
    const MentionAt m = mentionAt(text, cursor);
    if (m.atIndex < 0)
        return text;
    return text.left(m.atIndex) + u"@"_s + slug + u" "_s + text.mid(cursor);
}

QVariantList ChatEngine::searchChannels(const QString &query) const
{
    const QString q = query.trimmed().toLower();
    QVariantList out;
    for (const ChannelRow &r : m_channelModel.rows()) {
        if (r.header)
            continue;
        bool ok = q.isEmpty();
        if (!ok) { // a subsequence match, as a quick switcher does
            int qi = 0;
            const QString n = r.name.toLower();
            for (int i = 0; i < n.size() && qi < q.size(); ++i)
                if (n[i] == q[qi]) ++qi;
            ok = qi == q.size();
        }
        if (ok)
            out << QVariantMap{{u"channelId"_s, r.id}, {u"name"_s, r.name}, {u"section"_s, r.section}, {u"unread"_s, r.unread}};
    }
    return out;
}

void ChatEngine::copyText(const QString &text) const
{
    if (auto *cb = QGuiApplication::clipboard())
        cb->setText(text);
}

void ChatEngine::openLink(const QString &url) const
{
    const QUrl u(url);
    const QString s = u.scheme().toLower();
    if (u.isValid() && (s == u"http"_s || s == u"https"_s || s == u"mailto"_s))
        QDesktopServices::openUrl(u);
}

} // namespace Taos::Messages

namespace {
void registerMessagesTypes() { Taos::Messages::ChatEngine::registerTypes(); }
}
Q_COREAPP_STARTUP_FUNCTION(registerMessagesTypes)
