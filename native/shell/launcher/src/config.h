// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMutex>
#include <QString>
#include <QStringList>

namespace taos::search {

// The launcher's per-person settings, ~/.config/taos/launcher.json:
//   { "mentionAction": "send" | "send_and_open",        default "send"
//     "askCommand": ["some-cli", "{query}"] }           optional hand-off command for "Ask taOS"
// Missing or broken file = defaults. Re-read by load() (the launcher calls it each time it shows).
class LauncherConfig {
public:
    explicit LauncherConfig(const QString &path = defaultPath()) : m_path(path) { load(); }
    static QString defaultPath();
    void load();
    bool mentionOpensChat() const { QMutexLocker l(&m_mu); return m_sendAndOpen; }
    QStringList askCommand() const { QMutexLocker l(&m_mu); return m_ask; }
    void setMentionOpensChat(bool v) { QMutexLocker l(&m_mu); m_sendAndOpen = v; }   // tests
    void setAskCommand(const QStringList &c) { QMutexLocker l(&m_mu); m_ask = c; }   // tests
private:
    QString m_path;
    mutable QMutex m_mu;
    bool m_sendAndOpen = false;
    QStringList m_ask;
};

}  // namespace taos::search
