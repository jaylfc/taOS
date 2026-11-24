// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The Messages engine: everything the UI shows and does, with no UI in it. It holds
// no state the controller would not also hold, apart from drafts and the optimistic
// rows of messages in flight.
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QPointer>
#include <QSet>
#include <QTimer>

#include "chatlogic.h"
#include "chatmodels.h"
#include "taosqt/client.h"
#include "taosqt/eventstream.h"

namespace Taos::Messages {

class ChatEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(bool windowActive READ windowActive WRITE setWindowActive NOTIFY windowActiveChanged)
    Q_PROPERTY(QObject *channels READ channelsObject CONSTANT)
    Q_PROPERTY(QObject *messages READ messagesObject CONSTANT)
    Q_PROPERTY(QString selectedId READ selectedId NOTIFY selectedChanged)
    Q_PROPERTY(QString selectedName READ selectedName NOTIFY selectedChanged)
    Q_PROPERTY(QString selectedType READ selectedType NOTIFY selectedChanged)
    Q_PROPERTY(QString selectedTopic READ selectedTopic NOTIFY selectedChanged)
    Q_PROPERTY(QStringList selectedMembers READ selectedMembers NOTIFY selectedChanged)
    Q_PROPERTY(bool channelsLoaded READ channelsLoaded NOTIFY channelsChanged)
    Q_PROPERTY(bool channelsLoading READ channelsLoading NOTIFY channelsChanged)
    Q_PROPERTY(QString channelsError READ channelsError NOTIFY channelsChanged)
    Q_PROPERTY(bool messagesLoading READ messagesLoading NOTIFY messagesStateChanged)
    Q_PROPERTY(QString messagesError READ messagesError NOTIFY messagesStateChanged)
    Q_PROPERTY(bool hasOlder READ hasOlder NOTIFY messagesStateChanged)
    Q_PROPERTY(bool loadingOlder READ loadingOlder NOTIFY messagesStateChanged)
    Q_PROPERTY(bool live READ live NOTIFY liveChanged)
    Q_PROPERTY(QString typingText READ typingText NOTIFY typingChanged)
    Q_PROPERTY(QString sendError READ sendError NOTIFY sendErrorChanged)
    Q_PROPERTY(int totalUnread READ totalUnread NOTIFY channelsChanged)
    Q_PROPERTY(QString currentUserId READ currentUserId NOTIFY userChanged)
    Q_PROPERTY(QString stallText READ stallText NOTIFY stallChanged)
    Q_PROPERTY(bool stalled READ stalled NOTIFY stallChanged)
public:
    explicit ChatEngine(QObject *parent = nullptr);
    ~ChatEngine() override;
    /// Registers the QML types (`Taos.Messages 1.0`).
    static void registerTypes();

    /// For tests: use this client instead of the default one.
    void setClient(Taos::Client *c) { m_client = c; }

    bool running() const { return m_running; }
    void setRunning(bool on);
    bool windowActive() const { return m_active; }
    void setWindowActive(bool on);
    QObject *channelsObject() { return &m_channelModel; }
    QObject *messagesObject() { return &m_messageModel; }
    ChannelModel *channelModel() { return &m_channelModel; }
    MessageModel *messageModel() { return &m_messageModel; }
    QString selectedId() const { return m_selected; }
    QString selectedName() const;
    QString selectedType() const;
    QString selectedTopic() const;
    QStringList selectedMembers() const;
    bool channelsLoaded() const { return m_channelsLoaded; }
    bool channelsLoading() const { return m_channelsLoading; }
    QString channelsError() const { return m_channelsError; }
    bool messagesLoading() const { return m_messagesLoading; }
    QString messagesError() const { return m_messagesError; }
    bool hasOlder() const { return m_hasOlder; }
    bool loadingOlder() const { return m_loadingOlder; }
    bool live() const { return m_live; }
    QString typingText() const { return m_typingText; }
    QString sendError() const { return m_sendError; }
    int totalUnread() const;
    QString currentUserId() const { return m_userId; }
    QString stallText() const { return m_stallText; }
    bool stalled() const { return m_stalled; }

    Q_INVOKABLE void openChannel(const QString &id);
    Q_INVOKABLE void openAgent(const QString &name);
    /// Command line of the app: `--channel <id>`, `--channel=<id>`, `--agent <name>`.
    Q_INVOKABLE void applyArguments(const QStringList &args);
    Q_INVOKABLE void send(const QString &text);
    Q_INVOKABLE void retry(const QString &localId);
    Q_INVOKABLE void discard(const QString &localId);
    Q_INVOKABLE void loadOlder();
    Q_INVOKABLE void reloadMessages();
    Q_INVOKABLE void refresh();
    Q_INVOKABLE void markRead();
    Q_INVOKABLE void noteTyping();
    Q_INVOKABLE void toggleSection(const QString &section);
    Q_INVOKABLE QStringList mentionCandidates(const QString &text, int cursor) const;
    Q_INVOKABLE int mentionAtIndex(const QString &text, int cursor) const;
    Q_INVOKABLE QString insertMention(const QString &text, int cursor, const QString &slug) const;
    Q_INVOKABLE QVariantList searchChannels(const QString &query) const;
    Q_INVOKABLE QString draft(const QString &channelId) const { return m_drafts.value(channelId); }
    Q_INVOKABLE void setDraft(const QString &channelId, const QString &text);
    Q_INVOKABLE void copyText(const QString &text) const;
    Q_INVOKABLE void openLink(const QString &url) const;
    Q_INVOKABLE void clearSendError();

Q_SIGNALS:
    void runningChanged();
    void windowActiveChanged();
    void selectedChanged();
    void channelsChanged();
    void messagesStateChanged();
    void liveChanged();
    void typingChanged();
    void sendErrorChanged();
    void userChanged();
    void stallChanged();
    /// The open conversation got a message at the end (`own`: it is the person's). The view scrolls or counts it.
    void messageAppended(bool own);
    /// Someone else's message arrived in the open conversation (for the live region).
    void incomingMessage(const QString &author, const QString &text);
    /// The open conversation was (re)loaded: scroll to the end.
    void conversationLoaded();
    /// Older messages were put in front (keep the reading position).
    void olderLoaded(int count);

private:
    Taos::Client *client() const;
    void start();
    void stop();
    void fetchUser();
    void fetchChannels();
    void fetchUnread();
    void fetchAgents();
    void fetchMessages();
    void openStream();
    void onFrame(const QJsonObject &f);
    void rebuildChannels();
    int unreadOf(const QString &id) const { return m_unread.value(id); }
    QJsonObject channelJson(const QString &id) const;
    void setSendError(const QString &e);
    void updateTyping();
    void updateStall();
    void joinSelected();
    void sendLocal(const QString &localId, const QString &text);
    QString agentDisplayName(const QString &slug) const;
    void applyPendingOpen();
    void tickTimers();

    struct Typing { QString phase; QString detail; qint64 until = 0; bool agent = false; };

    QPointer<Taos::Client> m_client;
    bool m_running = false;
    bool m_active = true;
    ChannelModel m_channelModel;
    MessageModel m_messageModel;
    QJsonArray m_channels;
    QHash<QString, int> m_unread;
    QList<AgentInfo> m_agents;
    QSet<QString> m_archivedKeys;
    bool m_agentsLoaded = false;
    bool m_archivedLoaded = false;
    QSet<QString> m_collapsed;
    QSet<QString> m_working;
    QHash<QString, Typing> m_typing;
    QString m_selected;
    QString m_pendingChannel;
    QString m_pendingAgent;
    QString m_userId;
    bool m_channelsLoaded = false;
    bool m_channelsLoading = false;
    QString m_channelsError;
    bool m_messagesLoading = false;
    QString m_messagesError;
    bool m_hasOlder = false;
    bool m_loadingOlder = false;
    bool m_live = false;
    QString m_typingText;
    QString m_sendError;
    QHash<QString, QString> m_drafts;
    QPointer<Taos::EventStream> m_stream;
    int m_openSeq = 0;
    int m_localSeq = 0;
    qint64 m_lastTypingSent = 0;
    QTimer m_clock;
    QTimer m_poll;
    // stall watch (MessagesApp.stallWatch.ts)
    QString m_watchAgent, m_watchChannel, m_watchStream;
    qint64 m_watchActivity = 0;
    QString m_stallText;
    bool m_stalled = false;
    int m_unreadAtOpen = 0;
    bool m_unreadLoaded = false;
    bool m_awaitUnread = false;
    friend class TstMessages;
};

} // namespace Taos::Messages
