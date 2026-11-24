// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QList>
#include <QString>
#include <QStringList>
#include <functional>

namespace taos::search {

struct Agent {
    QString id;       // stable agent id
    QString name;     // the @handle is derived from this
    QString status;   // "online", "busy", "offline" or empty when unknown
};

// Where the person's agents come from. The real implementation belongs on top of the taos-client
// crate (native/taos-client) once it has an agents operation; today there is none, so the daemon
// runs with UnavailableDirectory and the launcher says so. agents() must be cheap and thread safe
// (it is called from the search worker threads); refresh() starts a background update.
class AgentDirectory {
public:
    virtual ~AgentDirectory() = default;
    virtual QList<Agent> agents() const = 0;
    virtual void refresh() {}
    // Non empty when the directory cannot work, in words the person can read.
    virtual QString unavailableReason() const { return {}; }
};

// Sends a message to agents. Called on the main thread; `done` is called once, on the main thread.
class ChatSender {
public:
    virtual ~ChatSender() = default;
    // Can one message go to several agents? When false only the first mention is used.
    virtual bool supportsMultipleRecipients() const { return false; }
    virtual void send(const QStringList &agentIds, const QString &text, std::function<void(bool ok, const QString &message)> done) = 0;
    // Open the chat window with this agent. False when there is nothing to open yet.
    virtual bool openChat(const QString &agentId) { Q_UNUSED(agentId); return false; }
};

class UnavailableDirectory : public AgentDirectory {
public:
    QList<Agent> agents() const override { return {}; }
    QString unavailableReason() const override {
        return QStringLiteral("Agents are not connected yet (the taOS client has no agents operation)");
    }
};

class UnavailableSender : public ChatSender {
public:
    void send(const QStringList &, const QString &, std::function<void(bool, const QString &)> done) override {
        done(false, QStringLiteral("Chat is not connected yet (the taOS client has no chat operation)"));
    }
};

// In memory, for tests and the offscreen renders.
class FakeDirectory : public AgentDirectory {
public:
    explicit FakeDirectory(QList<Agent> a = {}) : m_agents(std::move(a)) {}
    QList<Agent> agents() const override { return m_agents; }
    QList<Agent> m_agents;
};

class FakeSender : public ChatSender {
public:
    bool multi = false, canOpen = true, nextOk = true;
    QStringList lastIds, opened;
    QString lastText;
    bool supportsMultipleRecipients() const override { return multi; }
    void send(const QStringList &ids, const QString &text, std::function<void(bool, const QString &)> done) override {
        lastIds = ids; lastText = text;
        done(nextOk, nextOk ? QString() : QStringLiteral("could not send"));
    }
    bool openChat(const QString &id) override { if (!canOpen) return false; opened << id; return true; }
};

}  // namespace taos::search
