// SPDX-License-Identifier: AGPL-3.0-or-later
#include "webappsservice.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QPointer>
#include <QThread>
#include <QUrl>

using namespace Qt::StringLiterals;
using namespace TaosWebapp;

namespace Taos::Store {

QString relativeDay(const QDateTime &when, const QDateTime &now)
{
    if (!when.isValid())
        return QObject::tr("Never opened");
    const int days = when.toLocalTime().date().daysTo(now.toLocalTime().date());
    if (days <= 0)
        return QObject::tr("Today");
    if (days == 1)
        return QObject::tr("Yesterday");
    if (days < 14)
        return QObject::tr("%1 days ago").arg(days);
    return when.toLocalTime().date().toString(Qt::ISODate);
}

QString bytesText(qint64 bytes)
{
    if (bytes < 1024)
        return u"%1 KB"_s.arg(0);
    const double kb = bytes / 1024.0;
    if (kb < 1024)
        return u"%1 KB"_s.arg(qRound(kb));
    const double mb = kb / 1024.0;
    if (mb < 1024)
        return u"%1 MB"_s.arg(mb, 0, 'f', mb < 10 ? 1 : 0);
    return u"%1 GB"_s.arg(mb / 1024.0, 0, 'f', 1);
}

WebAppsService::WebAppsService(const Options &o, QObject *parent) : QObject(parent), m_o(o)
{
    m_poll.setInterval(2500);
    connect(&m_poll, &QTimer::timeout, this, [this] { scan(false); });   // the poll is cheap: who is open, nothing more
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(120);
    connect(&m_debounce, &QTimer::timeout, this, [this] { scan(true); });
    connect(&m_watch, &QFileSystemWatcher::directoryChanged, &m_debounce, qOverload<>(&QTimer::start));
    connect(&m_watch, &QFileSystemWatcher::fileChanged, &m_debounce, qOverload<>(&QTimer::start));
    QDir().mkpath(m_o.root);
    m_watch.addPath(m_o.root);
    refresh();
}

WebAppsService::~WebAppsService()
{
    ++m_gen;   // results of workers still running are dropped
    // A worker posts its result to the application: let it finish while the application is still alive.
    for (const QPointer<QThread> &t : std::as_const(m_threads))
        if (t)
            t->wait(15000);
}

void WebAppsService::setActive(bool on)
{
    if (m_active == on)
        return;
    m_active = on;
    if (on) {
        m_poll.start();
        scan(true);
    } else {
        m_poll.stop();
    }
    Q_EMIT activeChanged();
}

QHash<QString, int> WebAppsService::instanceCounts() const
{
    QHash<QString, int> n;
    for (const QVariant &v : m_rows)
        n[v.toMap().value(u"source"_s).toString()]++;
    return n;
}

QVariantMap WebAppsService::instance(const QString &id) const
{
    for (const QVariant &v : m_rows)
        if (v.toMap().value(u"id"_s).toString() == id)
            return v.toMap();
    return {};
}

void WebAppsService::refresh()
{
    scan(true);
}

void WebAppsService::scan(bool full)
{
    if (m_scanning) {
        m_rescan = true;
        m_rescanFull = m_rescanFull || full;
        return;
    }
    m_scanning = true;
    const Options o = m_o;
    const quint64 gen = m_gen;
    const QHash<QString, qint64> known = m_sizes;
    QPointer<WebAppsService> self(this);
    QThread *t = QThread::create([o, gen, self, full, known] {
        Engine e(o);
        if (full)
            e.syncLaunchers();
        QVariantList rows;
        QHash<QString, qint64> sizes;
        for (const Instance &i : e.list()) {
            QVariantMap m = i.toJson().toVariantMap();
            m[u"running"_s] = e.isRunning(i.id);
            // walking a profile is not cheap: only a full scan (opening, a change, an action) does it
            const qint64 sz = full || !known.contains(i.id) ? e.profileSize(i.id) : known.value(i.id);
            sizes.insert(i.id, sz);
            m[u"sizeBytes"_s] = sz;
            m[u"sizeText"_s] = bytesText(sz);
            m[u"host"_s] = Engine::hostLabel(QUrl(i.url));
            m[u"iconUrl"_s] = i.icon.isEmpty() ? QUrl() : QUrl::fromLocalFile(i.icon);
            m[u"letter"_s] = i.name.isEmpty() ? u"?"_s : i.name.left(1).toUpper();
            m[u"lastUsedDate"_s] = i.lastUsed;
            rows << m;
        }
        const Result d = e.doctor(false);   // no browser is run for its version here
        const bool ok = d.ok;
        const QString text = ok ? QString() : d.error;
        if (!QCoreApplication::instance())
            return;
        QMetaObject::invokeMethod(QCoreApplication::instance(), [self, gen, rows, ok, text, sizes] {
            if (!self || self->m_gen != gen)
                return;
            self->m_scanning = false;
            self->m_sizes = sizes;
            QVariantList withText = rows;
            for (QVariant &v : withText) {
                QVariantMap m = v.toMap();
                m[u"lastUsedText"_s] = relativeDay(m.value(u"lastUsedDate"_s).toDateTime(), self->now());
                m.remove(u"lastUsedDate"_s);
                v = m;
            }
            if (withText != self->m_rows) {
                self->m_rows = withText;
                ++self->m_revision;
                Q_EMIT self->instancesChanged();
            }
            if (ok != self->m_browserOk || text != self->m_browserText) {
                self->m_browserOk = ok;
                self->m_browserText = text;
                Q_EMIT self->browserChanged();
            }
            const QString idx = self->m_o.root + u"/index.json"_s;
            if (QFileInfo::exists(idx) && !self->m_watch.files().contains(idx))
                self->m_watch.addPath(idx);
            if (self->m_rescan) {
                const bool again = self->m_rescanFull;
                self->m_rescan = self->m_rescanFull = false;
                self->scan(again);
            }
        });
    });
    connect(t, &QThread::finished, t, &QObject::deleteLater);
    m_threads.removeAll(nullptr);
    m_threads << t;
    t->start();
}

QString WebAppsService::checkUrl(const QString &url, bool allowHttp) const
{
    QString err;
    const QUrl u = Engine::normalizeUrl(url, allowHttp, &err);
    return u.isValid() ? QString() : err;
}

void WebAppsService::run(const QString &op, const QString &id, const QString &busy, std::function<Result()> work,
                         std::function<QString(const Result &)> okText)
{
    if (!id.isEmpty()) {
        if (m_busy.contains(id))
            return;
        m_busy.insert(id, busy);
        ++m_revision;
        Q_EMIT instancesChanged();
    } else if (op == u"add"_s) {
        if (m_adding)
            return;
        m_adding = true;
        ++m_revision;
        Q_EMIT instancesChanged();
    }
    QPointer<WebAppsService> self(this);
    const quint64 gen = m_gen;
    QThread *t = QThread::create([=] {
        const Result r = work();
        if (!QCoreApplication::instance())
            return;
        QMetaObject::invokeMethod(QCoreApplication::instance(), [=] {
            if (!self || self->m_gen != gen)
                return;
            if (!id.isEmpty())
                self->m_busy.remove(id);
            if (op == u"add"_s)
                self->m_adding = false;
            const QString text = r.ok ? okText(r) : r.error;
            ++self->m_revision;
            self->scan(true);
            Q_EMIT self->instancesChanged();
            Q_EMIT self->finished(op, r.ok, r.ok ? r.data.value(u"id"_s).toString(id) : id, text, r.code);
        });
    });
    connect(t, &QThread::finished, t, &QObject::deleteLater);
    m_threads.removeAll(nullptr);
    m_threads << t;
    t->start();
}

void WebAppsService::add(const QString &url, const QString &name, const QString &storeId, bool autoIcon, bool allowHttp)
{
    const Options o = m_o;
    AddRequest req;
    req.url = url;
    req.name = name;
    req.icon = autoIcon ? u"auto"_s : QString();
    req.source = storeId.isEmpty() ? u"custom"_s : storeId;
    req.allowHttp = allowHttp;
    run(u"add"_s, {}, {}, [o, req] { Engine e(o); return e.add(req); },
        [](const Result &r) { return tr("Added %1.").arg(r.data.value(u"name"_s).toString()); });
}

void WebAppsService::launch(const QString &id)
{
    const Options o = m_o;
    const QString name = instance(id).value(u"name"_s).toString();
    run(u"launch"_s, id, tr("Opening"), [o, id] { Engine e(o); return e.launch(id); },
        [name](const Result &) { return tr("Opening %1.").arg(name); });
}

void WebAppsService::rename(const QString &id, const QString &name)
{
    const Options o = m_o;
    run(u"rename"_s, id, tr("Renaming"), [o, id, name] { Engine e(o); return e.rename(id, name); },
        [](const Result &r) { return tr("Renamed to %1.").arg(r.data.value(u"name"_s).toString()); });
}

void WebAppsService::clone(const QString &id, const QString &name)
{
    const Options o = m_o;
    run(u"clone"_s, id, tr("Cloning"), [o, id, name] { Engine e(o); return e.clone(id, name); },
        [](const Result &r) { return tr("Cloned as %1. It is signed in as the original was.").arg(r.data.value(u"name"_s).toString()); });
}

void WebAppsService::clearData(const QString &id)
{
    const Options o = m_o;
    const QString name = instance(id).value(u"name"_s).toString();
    run(u"clear"_s, id, tr("Clearing data"), [o, id] { Engine e(o); return e.clearData(id); },
        [name](const Result &) { return tr("Cleared the data of %1. It will ask you to sign in again.").arg(name); });
}

void WebAppsService::remove(const QString &id, bool keepData)
{
    const Options o = m_o;
    const QString name = instance(id).value(u"name"_s).toString();
    run(u"remove"_s, id, tr("Removing"), [o, id, keepData] { Engine e(o); return e.remove(id, keepData); },
        [name](const Result &) { return tr("Removed %1.").arg(name); });
}

} // namespace Taos::Store
