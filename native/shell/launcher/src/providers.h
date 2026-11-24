#pragma once
#include <QDateTime>
#include <QMutex>
#include <functional>
#include <memory>

#include "desktopentry.h"
#include "frecency.h"
#include "provider.h"

namespace taos::search {

// 1. Applications: freedesktop desktop entries, fuzzy name/keyword match, frecency.
class AppsProvider : public Provider {
public:
    explicit AppsProvider(std::shared_ptr<Frecency> frecency, QStringList dirs = {}, QString locale = {}, QStringList desktops = {});
    QString id() const override { return QStringLiteral("apps"); }
    QString name() const override { return QStringLiteral("Applications"); }
    void prepare() override;
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    // Icon for a window app id (StartupWMClass is not parsed; desktop id and Icon= are matched).
    QString iconForAppId(const QString &appId) const;
    int entryCount() const;
    QList<DesktopEntry> entries();   // a copy; loads on first use
    void reload();   // rescan now (tests, and after installs)
private:
    void ensureLoaded();
    qint64 dirsStamp() const;
    Result makeResult(const DesktopEntry &e, double score) const;
    std::shared_ptr<Frecency> m_frec;
    QStringList m_dirs, m_desktops;
    QString m_locale;
    mutable QMutex m_mu;
    QList<DesktopEntry> m_entries;
    bool m_loaded = false;
    qint64 m_stamp = 0;
};

// 2. Open windows, through the `taos-window list --json` CLI contract.
class WindowsProvider : public Provider {
public:
    using Fetch = std::function<QByteArray(bool *ok)>;   // the JSON the CLI printed
    explicit WindowsProvider(Fetch fetch = {}, std::function<QString(const QString &)> iconFor = {});
    QString id() const override { return QStringLiteral("windows"); }
    QString name() const override { return QStringLiteral("Open windows"); }
    int timeBudgetMs() const override { return 450; }
    bool available() const override;
    void prepare() override;
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    static QList<Result> parse(const QByteArray &json, const QString &text, const std::function<QString(const QString &)> &iconFor);
private:
    QByteArray cached(bool *ok);
    Fetch m_fetch;
    bool m_defaultFetch;
    std::function<QString(const QString &)> m_iconFor;
    QMutex m_mu;
    QByteArray m_cache;
    qint64 m_at = 0;
};

// 3. Built in commands and actions. Each is offered only when the CLI it needs exists.
class CommandsProvider : public Provider {
public:
    // `exists` answers "is this executable on PATH" (injectable for tests).
    explicit CommandsProvider(std::function<bool(const QString &)> exists = {}, std::shared_ptr<Frecency> frecency = {});
    QString id() const override { return QStringLiteral("commands"); }
    QString name() const override { return QStringLiteral("Actions"); }
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
    // Every command offered right now (for tests and the CLI).
    QList<Result> offered() const;
private:
    std::function<bool(const QString &)> m_exists;
    std::shared_ptr<Frecency> m_frec;
};

// 4. Quick maths. Result row, Enter copies.
class CalcProvider : public Provider {
public:
    QString id() const override { return QStringLiteral("calc"); }
    QString name() const override { return QStringLiteral("Calculator"); }
    int timeBudgetMs() const override { return 60; }
    QList<Result> query(const QString &text, const CancelToken &cancel) override;
};

bool executableExists(const QString &name);

}  // namespace taos::search
