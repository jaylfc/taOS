#include "appsoverview.h"

#include <QDir>
#include <QRegularExpression>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcess>
#include <QSettings>
#include <QStandardPaths>
#include <QUrl>

using namespace Qt::StringLiterals;

namespace Taos::Store {

DesktopEntry DesktopEntry::parse(const QString &path)
{
    DesktopEntry e;
    e.file = path;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return e;
    bool inEntry = false;
    for (const QByteArray &raw : f.readAll().split('\n')) {
        const QString line = QString::fromUtf8(raw).trimmed();
        if (line.startsWith(u'[')) {
            inEntry = line == u"[Desktop Entry]"_s;
            continue;
        }
        if (!inEntry || line.isEmpty() || line.startsWith(u'#'))
            continue;
        const int eq = line.indexOf(u'=');
        if (eq <= 0)
            continue;
        const QString k = line.left(eq).trimmed(), v = line.mid(eq + 1).trimmed();
        if (k == u"Name"_s)
            e.name = v;
        else if (k == u"Comment"_s)
            e.comment = v;
        else if (k == u"Icon"_s)
            e.icon = v;
        else if (k == u"Exec"_s)
            e.exec = v;
        else if (k == u"X-taOS-WebApp"_s)
            e.webApp = v;
        else if (k == u"X-taOS-AppId"_s)
            e.appId = v;
        else if (k == u"NoDisplay"_s)
            e.noDisplay = v == u"true"_s;
    }
    return e;
}

QStringList DesktopEntry::command() const
{
    QString x = exec;
    x.replace(QRegularExpression(u"%[a-zA-Z]"_s), QString());
    return QProcess::splitCommand(x.trimmed());
}

AppsOverview::AppsOverview(QObject *parent) : QObject(parent)
{
    m_web = new WebAppsService(TaosWebapp::Options::fromEnvironment(), this);
    connect(m_web, &WebAppsService::instancesChanged, this, &AppsOverview::changed);
    connect(m_web, &WebAppsService::browserChanged, this, &AppsOverview::changed);
    rescan();
}

void AppsOverview::setWebappOptions(const TaosWebapp::Options &o)
{
    delete m_web;
    m_web = new WebAppsService(o, this);
    connect(m_web, &WebAppsService::instancesChanged, this, &AppsOverview::changed);
    connect(m_web, &WebAppsService::browserChanged, this, &AppsOverview::changed);
    m_web->setActive(m_active);
    Q_EMIT changed();
}

Taos::Client *AppsOverview::client() const
{
    return m_client ? m_client.data() : Taos::defaultClient();
}

QStringList AppsOverview::defaultDirs() const
{
    QStringList out;
    const QString home = qEnvironmentVariable("XDG_DATA_HOME", QDir::homePath() + u"/.local/share"_s);
    out << home + u"/applications"_s;
    QString dirs = qEnvironmentVariable("XDG_DATA_DIRS");
    if (dirs.isEmpty())
        dirs = u"/usr/local/share:/usr/share"_s;
    for (const QString &d : dirs.split(u':', Qt::SkipEmptyParts))
        out << d + u"/applications"_s;
    return out;
}

void AppsOverview::rescan()
{
    const QStringList dirs = m_dirs.isEmpty() ? defaultDirs() : m_dirs;
    QHash<QString, DesktopEntry> found;
    for (const QString &d : dirs) {
        for (const QString &fn : QDir(d).entryList({u"*.desktop"_s}, QDir::Files)) {
            DesktopEntry e = DesktopEntry::parse(d + u'/' + fn);
            // taOS's own apps: they name their app id. Web app instances have their own list.
            if (e.appId.isEmpty() || !e.webApp.isEmpty() || e.appId.startsWith(u"taos-webapp-"_s) || e.noDisplay || e.name.isEmpty())
                continue;
            if (!found.contains(e.appId))   // the person's own folder comes first
                found.insert(e.appId, e);
        }
    }
    m_entries = found;
    QList<DesktopEntry> sorted = found.values();
    std::sort(sorted.begin(), sorted.end(), [](const DesktopEntry &a, const DesktopEntry &b) { return a.name.localeAwareCompare(b.name) < 0; });
    m_native.clear();
    for (const DesktopEntry &e : sorted)
        m_native << QVariantMap{{u"id"_s, e.appId}, {u"name"_s, e.name}, {u"comment"_s, e.comment}, {u"icon"_s, e.icon},
                                {u"letter"_s, e.name.left(1).toUpper()}, {u"canOpen"_s, !e.command().isEmpty()}};
    ++m_revision;
    Q_EMIT changed();
}

void AppsOverview::setActive(bool on)
{
    if (m_active == on)
        return;
    m_active = on;
    m_web->setActive(on);
    if (on) {
        refresh();
        // the controller answers once a person is signed in: ask again then
        if (!m_hooked && client()) {
            m_hooked = true;
            connect(client(), &Taos::Client::credentialsChanged, this, [this] { if (m_active) refresh(); });
        }
    }
    Q_EMIT activeChanged();
}

void AppsOverview::refresh()
{
    rescan();
    m_web->refresh();
    Taos::Client *c = client();
    if (!c)
        return;
    c->http(u"GET"_s, u"/api/store/catalog"_s)->then(this, [this, c](const Taos::Result &catalog) {
        QHash<QString, QJsonObject> byId;
        if (catalog.ok())
            for (const QJsonValue &v : catalog.value.toArray())
                byId.insert(v.toObject().value(u"id"_s).toString(), v.toObject());
        c->http(u"GET"_s, u"/api/store/installed-v2"_s)->then(this, [this, byId](const Taos::Result &r) {
            m_containers.clear();
            if (!r.ok()) {
                m_containersLoaded = false;
                m_containersError = r.error.userMessage();
            } else {
                m_containersLoaded = true;
                m_containersError.clear();
                for (const QJsonValue &v : r.value.toObject().value(u"installed"_s).toArray()) {
                    const QJsonObject o = v.toObject();
                    const QString id = o.value(u"app_id"_s).toString();
                    const QJsonObject cat = byId.value(id);
                    QString nice = id;
                    nice.replace(u'-', u' ');
                    if (!nice.isEmpty())
                        nice[0] = nice[0].toUpper();
                    const QString name = cat.value(u"name"_s).toString(nice);
                    const QString host = o.value(u"runtime_host"_s).toString();
                    const int port = o.value(u"runtime_port"_s).toInt();
                    QStringList sub;
                    if (!o.value(u"version"_s).toString().isEmpty())
                        sub << u"v"_s + o.value(u"version"_s).toString();
                    if (!o.value(u"runtime_backend"_s).toString().isEmpty())
                        sub << o.value(u"runtime_backend"_s).toString();
                    if (!host.isEmpty() && port > 0)
                        sub << u"%1:%2"_s.arg(host).arg(port);
                    m_containers << QVariantMap{{u"id"_s, id}, {u"name"_s, name}, {u"letter"_s, name.left(1).toUpper()},
                                                {u"subtitle"_s, sub.join(u" · "_s)}, {u"kind"_s, cat.value(u"type"_s).toString()}};
                }
                std::sort(m_containers.begin(), m_containers.end(), [](const QVariant &a, const QVariant &b) {
                    return a.toMap().value(u"name"_s).toString().localeAwareCompare(b.toMap().value(u"name"_s).toString()) < 0;
                });
            }
            ++m_revision;
            Q_EMIT changed();
        });
    });
}

bool AppsOverview::openNative(const QString &appId)
{
    const DesktopEntry e = m_entries.value(appId);
    const QStringList cmd = e.command();
    if (cmd.isEmpty())
        return false;
    const QString program = cmd.first();
    const QStringList args = cmd.mid(1);
    if (m_launcher)
        return m_launcher(program, args);
    return QProcess::startDetached(program, args);
}

bool AppsOverview::openInStore(const QString &appId)
{
    const QStringList args = {u"--app"_s, appId};
    if (m_launcher)
        return m_launcher(u"taos-store"_s, args);
    return QProcess::startDetached(u"taos-store"_s, args);
}

bool AppsOverview::openStore(const QString &section)
{
    const QStringList args = {u"--section"_s, section};
    if (m_launcher)
        return m_launcher(u"taos-store"_s, args);
    return QProcess::startDetached(u"taos-store"_s, args);
}

} // namespace Taos::Store
