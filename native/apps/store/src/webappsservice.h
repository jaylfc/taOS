// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The face of the web apps engine for the Store and Settings: the instances as rows, and every action run off the
// GUI thread (an icon fetch or a profile copy can take seconds). Follows the data folder, so an instance added
// by an agent through `taos-webapp` shows up here at once.
#include <QDateTime>
#include <QFileSystemWatcher>
#include <QObject>
#include <QPointer>
#include <QThread>
#include <QSet>
#include <QTimer>
#include <QVariantList>

#include "webapp.h"

namespace Taos::Store {

/// "Never opened", "Today", "Yesterday", "3 days ago", else the date.
QString relativeDay(const QDateTime &when, const QDateTime &now);
/// "0 KB", "12 MB", "1.4 GB".
QString bytesText(qint64 bytes);

class WebAppsService : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList instances READ instances NOTIFY instancesChanged)
    Q_PROPERTY(bool browserAvailable READ browserAvailable NOTIFY browserChanged)
    Q_PROPERTY(QString browserText READ browserText NOTIFY browserChanged)
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
    Q_PROPERTY(int revision READ revision NOTIFY instancesChanged)
public:
    explicit WebAppsService(const TaosWebapp::Options &o = TaosWebapp::Options::fromEnvironment(), QObject *parent = nullptr);
    ~WebAppsService() override;

    QVariantList instances() const { return m_rows; }
    bool browserAvailable() const { return m_browserOk; }
    QString browserText() const { return m_browserText; }
    bool active() const { return m_active; }
    void setActive(bool on);
    int revision() const { return m_revision; }
    QHash<QString, int> instanceCounts() const;
    void setClock(std::function<QDateTime()> c) { m_clock = std::move(c); }

    Q_INVOKABLE QVariantMap instance(const QString &id) const;
    Q_INVOKABLE bool isBusy(const QString &id) const { return m_busy.contains(id); }
    Q_INVOKABLE QString busyText(const QString &id) const { return m_busy.value(id); }
    /// True while an add is running (there is no id yet).
    Q_INVOKABLE bool adding() const { return m_adding; }

    Q_INVOKABLE void refresh();
    /// Normalises and checks the address the way `add` will; "" when fine, else the sentence to show.
    Q_INVOKABLE QString checkUrl(const QString &url, bool allowHttp = false) const;
    Q_INVOKABLE void add(const QString &url, const QString &name, const QString &storeId = {}, bool autoIcon = true,
                         bool allowHttp = false);
    Q_INVOKABLE void launch(const QString &id);
    Q_INVOKABLE void rename(const QString &id, const QString &name);
    Q_INVOKABLE void clone(const QString &id, const QString &name = {});
    Q_INVOKABLE void clearData(const QString &id);
    Q_INVOKABLE void remove(const QString &id, bool keepData = false);

    const TaosWebapp::Options &options() const { return m_o; }

Q_SIGNALS:
    void instancesChanged();
    void browserChanged();
    void activeChanged();
    /// op: add, launch, rename, clone, clear, remove. `text` is the sentence to show either way.
    void finished(const QString &op, bool ok, const QString &id, const QString &text, const QString &code);

private:
    void run(const QString &op, const QString &id, const QString &busy, std::function<TaosWebapp::Result()> work,
             std::function<QString(const TaosWebapp::Result &)> okText);
    void scan(bool full = true);
    QDateTime now() const { return m_clock ? m_clock() : QDateTime::currentDateTimeUtc(); }

    TaosWebapp::Options m_o;
    QVariantList m_rows;
    QHash<QString, QString> m_busy;
    QFileSystemWatcher m_watch;
    QTimer m_poll, m_debounce;
    std::function<QDateTime()> m_clock;
    bool m_active = false, m_browserOk = false, m_adding = false, m_scanning = false, m_rescan = false, m_rescanFull = false;
    QHash<QString, qint64> m_sizes;   // sizes found by the last full scan: the poll reuses them
    QString m_browserText;
    int m_revision = 0;
    quint64 m_gen = 0;
    QList<QPointer<QThread>> m_threads;   // workers still running: the destructor waits for them
};

} // namespace Taos::Store
