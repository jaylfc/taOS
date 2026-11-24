// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMutex>
#include <memory>

#include "agents.h"
#include "config.h"
#include "fileindex.h"
#include "providers.h"

namespace taos::search {

// 5. Files: name search under the home folder with xdg-open. Content search is out of scope.
class FilesProvider : public Provider {
public:
    explicit FilesProvider(std::shared_ptr<FileIndex> index) : m_index(std::move(index)) {}
    QString id() const override { return QStringLiteral("files"); }
    QString name() const override { return QStringLiteral("Files"); }
    int timeBudgetMs() const override { return 250; }
    bool available() const override { return executableExists(QStringLiteral("xdg-open")) || m_forceAvailable; }
    void prepare() override;
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    bool m_forceAvailable = false;   // tests
private:
    std::shared_ptr<FileIndex> m_index;
    bool m_started = false;
};

// 6. Settings pages. Each opens the Settings app at its section (`taos-settings --section <id>`); a stub (marked
// in its subtitle) while the app is not installed, or for a page the app does not have yet.
class SettingsProvider : public Provider {
public:
    explicit SettingsProvider(std::function<bool(const QString &)> exists = {}) : m_exists(exists ? std::move(exists) : std::function<bool(const QString &)>(executableExists)) {}
    QString id() const override { return QStringLiteral("settings"); }
    QString name() const override { return QStringLiteral("Settings"); }
    int timeBudgetMs() const override { return 60; }
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
private:
    std::function<bool(const QString &)> m_exists;
};

// 7. Ask taOS: the last row, always. Hand-off, in order: the configured askCommand (placeholder
// {query}); else a Messages/chat desktop entry is opened with the question put on the clipboard;
// else a stub that says agent chat is not set up. NO controller API is called from here.
class AskProvider : public Provider {
public:
    AskProvider(std::shared_ptr<LauncherConfig> cfg, std::function<QList<DesktopEntry>()> entries) : m_cfg(std::move(cfg)), m_entries(std::move(entries)) {}
    QString id() const override { return QStringLiteral("ask"); }
    QString name() const override { return QStringLiteral("Ask taOS"); }
    int timeBudgetMs() const override { return 60; }
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    static QStringList chatDesktopIds();
private:
    std::shared_ptr<LauncherConfig> m_cfg;
    std::function<QList<DesktopEntry>()> m_entries;
};

// @agent mentions. "@" lists agents, "@par" filters, "@parker some text" offers to send. Needs an
// AgentDirectory; without one it shows a single explanatory row.
class MentionProvider : public Provider {
public:
    MentionProvider(std::shared_ptr<AgentDirectory> dir, std::shared_ptr<LauncherConfig> cfg, bool multiSend)
        : m_dir(std::move(dir)), m_cfg(std::move(cfg)), m_multi(multiSend) {}
    QString id() const override { return QStringLiteral("mentions"); }
    QString name() const override { return QStringLiteral("Agents"); }
    int timeBudgetMs() const override { return 80; }
    void prepare() override { m_dir->refresh(); }
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    static QString handleOf(const QString &name);
private:
    std::shared_ptr<AgentDirectory> m_dir;
    std::shared_ptr<LauncherConfig> m_cfg;
    bool m_multi;
};

}  // namespace taos::search
