#include "storeengine.h"

#include <QDesktopServices>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QUrl>
#include <QtQml/qqml.h>

#include "appsoverview.h"
#include "theme.h"
#include "thememanager.h"

using namespace Qt::StringLiterals;

namespace Taos::Store {

// ================================================================ ThemesService

ThemesService::ThemesService(QObject *parent) : ThemesService(Paths(), parent) {}

ThemesService::ThemesService(const Paths &p, QObject *parent) : QObject(parent)
{
    m_theme = std::make_unique<Theme>();
    ThemeManager::Options o;
    o.watch = p.watch;
    if (!p.useDefaults) {
        o.appearancePath = p.appearancePath;
        o.systemPath = p.appearancePath + u".system"_s;   // never the real /etc/taos
        o.mirrorDir = p.mirrorDir;
        o.themeDirs = p.themeDirs;
        o.builtinDir = p.builtinDir;
    }
    m_mgr = std::make_unique<ThemeManager>(m_theme.get(), o);
    m_mgr->start();
    connect(m_mgr.get(), &ThemeManager::appearanceChanged, this, &ThemesService::changed);
    connect(m_mgr.get(), &ThemeManager::themesChanged, this, &ThemesService::changed);
}

ThemesService::~ThemesService() = default;

QString ThemesService::originOf(const QString &id) const
{
    static const QStringList shipped = {u"graphite"_s, u"graphite-ice"_s, u"graphite-warm"_s};
    return shipped.contains(id) ? tr("Built in") : tr("Yours");
}

QVariantList ThemesService::themes() const
{
    QVariantList out;
    for (const QVariant &v : m_mgr->themes()) {
        QVariantMap m = v.toMap();
        m[u"origin"_s] = originOf(m.value(u"id"_s).toString());
        out << m;
    }
    return out;
}

QString ThemesService::mode() const
{
    return m_mgr->mode();
}

bool ThemesService::apply(const QString &id)
{
    return m_mgr->setTheme(id);
}

// ================================================================ StoreEngine

StoreEngine::StoreEngine(QObject *parent) : QObject(parent)
{
    m_web = new WebAppsService(TaosWebapp::Options::fromEnvironment(), this);
    m_themes = std::make_unique<ThemesService>();
    connect(m_web, &WebAppsService::instancesChanged, this, [this] {
        m_cat.setInstanceCounts(m_web->instanceCounts());
        bump();
    });
    connect(m_web, &WebAppsService::browserChanged, this, [this] { bump(); });
    connect(m_web, &WebAppsService::finished, this, [this](const QString &op, bool ok, const QString &, const QString &text, const QString &) {
        Q_EMIT toast(text);
        if (op == u"add"_s && ok)
            bump();
    });
    connect(m_themes.get(), &ThemesService::changed, this, [this] { bump(); });
    m_poll.setInterval(1500);
    connect(&m_poll, &QTimer::timeout, this, &StoreEngine::pollProgress);
}

StoreEngine::~StoreEngine() = default;

void StoreEngine::setWebappOptions(const TaosWebapp::Options &o)
{
    delete m_web;
    m_web = new WebAppsService(o, this);
    connect(m_web, &WebAppsService::instancesChanged, this, [this] {
        m_cat.setInstanceCounts(m_web->instanceCounts());
        bump();
    });
    connect(m_web, &WebAppsService::browserChanged, this, [this] { bump(); });
    connect(m_web, &WebAppsService::finished, this, [this](const QString &op, bool ok, const QString &, const QString &text, const QString &) {
        Q_EMIT toast(text);
        if (op == u"add"_s && ok)
            bump();
    });
    m_cat.setInstanceCounts(m_web->instanceCounts());
}

void StoreEngine::setThemePaths(const ThemesService::Paths &p)
{
    m_themes = std::make_unique<ThemesService>(p);
    connect(m_themes.get(), &ThemesService::changed, this, [this] { bump(); });
}

void StoreEngine::setBuiltinWebapps(const QJsonArray &apps)
{
    m_haveBuiltin = true;
    m_cat.setBuiltinWebapps(apps);
    bump();
}

void StoreEngine::loadBuiltinWebapps()
{
    if (m_haveBuiltin)
        return;
    QString path = qEnvironmentVariable("TAOS_STORE_WEBAPPS");
    QFile f(path.isEmpty() ? u":/my.taos.store/catalog/webapps.json"_s : path);
    if (f.open(QIODevice::ReadOnly))
        setBuiltinWebapps(QJsonDocument::fromJson(f.readAll()).object().value(u"apps"_s).toArray());
}

QVariantList StoreEngine::sections() const
{
    const struct { const char16_t *id; const char *title; const char16_t *group; } s[] = {
        {u"discover", QT_TR_NOOP("Discover"), u""}, {u"apps", QT_TR_NOOP("Apps"), u""}, {u"webapps", QT_TR_NOOP("Web apps"), u""},
        {u"models", QT_TR_NOOP("Models"), u""}, {u"themes", QT_TR_NOOP("Themes"), u""}, {u"plugins", QT_TR_NOOP("Plugins"), u""},
        {u"installed", QT_TR_NOOP("Installed"), u"library"}, {u"updates", QT_TR_NOOP("Updates"), u"library"}};
    QVariantList out;
    for (const auto &e : s)
        out << QVariantMap{{u"id"_s, QString::fromUtf16(e.id)}, {u"title"_s, tr(e.title)}, {u"group"_s, QString::fromUtf16(e.group)}};
    return out;
}

Taos::Client *StoreEngine::client() const
{
    if (m_client)
        return m_client;
    return m_session ? m_session->client() : Taos::defaultClient();
}

bool StoreEngine::isAdmin() const
{
    return m_session && m_session->user().value(u"is_admin"_s).toBool();
}

void StoreEngine::setSession(Taos::Session *s)
{
    if (m_session == s)
        return;
    m_session = s;
    if (s) {
        connect(s, &Taos::Session::stateChanged, this, [this] {
            if (m_running && m_session->state() == Taos::Session::SignedIn)
                refresh();
            Q_EMIT stateChanged();
        });
        connect(s, &Taos::Session::userChanged, this, [this] { Q_EMIT stateChanged(); });
    }
    Q_EMIT sessionChanged();
}

void StoreEngine::setRunning(bool on)
{
    if (m_running == on)
        return;
    m_running = on;
    m_web->setActive(on);
    if (on) {
        loadBuiltinWebapps();
        refresh();
    } else {
        m_poll.stop();
    }
    Q_EMIT runningChanged();
}

void StoreEngine::bump()
{
    ++m_revision;
    Q_EMIT revisionChanged();
}

void StoreEngine::fail(const QString &, const Taos::Error &e)
{
    m_errorText = e.userMessage();
    switch (e.kind()) {
    case Taos::Error::Forbidden: m_errorKind = u"forbidden"_s; break;
    case Taos::Error::Unauthenticated: m_errorKind = u"unauthenticated"_s; break;
    case Taos::Error::Network: m_errorKind = u"network"_s; break;
    default: m_errorKind = u"other"_s;
    }
    Q_EMIT stateChanged();
}

// ---------------------------------------------------------------- catalogue

void StoreEngine::refresh()
{
    Taos::Client *c = client();
    if (!c)
        return;
    ++m_loading;
    Q_EMIT stateChanged();
    c->http(u"GET"_s, u"/api/store/catalog"_s)->then(this, [this](const Taos::Result &r) {
        --m_loading;
        if (r.ok() && r.value.isArray()) {
            m_cat.setServer(r.value.toArray());
            m_loaded = true;
            m_errorText.clear();
            m_errorKind.clear();
            Q_EMIT stateChanged();
        } else if (!r.ok()) {
            fail(u"catalog"_s, r.error);
        } else {
            fail(u"catalog"_s, Taos::Error(Taos::Error::Decode, u"The catalogue was not a list."_s));
        }
        m_cat.setInstanceCounts(m_web->instanceCounts());
        bump();
        const QSet<QString> wanted = m_detailWanted;
        m_detailWanted.clear();
        for (const QString &id : wanted)
            loadDetail(id);
    });
    fetchInstalled();
    fetchModels();
    fetchHardware();
}

void StoreEngine::fetchInstalled()
{
    client()->http(u"GET"_s, u"/api/store/installed-v2"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        QSet<QString> ids;
        m_runtime.clear();
        for (const QJsonValue &v : r.value.toObject().value(u"installed"_s).toArray()) {
            const QJsonObject o = v.toObject();
            const QString id = o.value(u"app_id"_s).toString();
            if (id.isEmpty())
                continue;
            ids.insert(id);
            m_runtime.insert(id, o.toVariantMap());
        }
        m_cat.setInstalled(ids);
        bump();
    });
}

void StoreEngine::fetchModels()
{
    client()->http(u"GET"_s, u"/api/models"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        const QJsonArray models = r.value.toObject().value(u"models"_s).toArray();
        m_modelRows.clear();
        for (const QJsonValue &v : models)
            m_modelRows.insert(v.toObject().value(u"id"_s).toString(), v.toObject());
        m_cat.setModelFit(models);
        bump();
    });
}

void StoreEngine::fetchHardware()
{
    client()->http(u"GET"_s, u"/api/hardware"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return;
        m_profile = r.value.toObject().value(u"profile_id"_s).toString();
        m_cat.setProfile(m_profile);
        Q_EMIT stateChanged();
        bump();
    });
}

QVariantList StoreEngine::list(const QString &section, const QString &query, const QString &chip) const
{
    if (section == u"discover"_s)
        return {};
    if (section == u"search"_s)
        return m_cat.search(query);
    return m_cat.section(section, query, chip);
}

int StoreEngine::count(const QString &section) const
{
    if (section == u"installed"_s)
        return installedRows().size();
    if (section == u"themes"_s)
        return m_themes->themes().size();
    return m_cat.count(section);
}

void StoreEngine::loadDetail(const QString &id)
{
    if (m_detail.contains(id) || !client())
        return;
    if (!m_cat.has(id)) {
        m_detailWanted.insert(id);   // asked for before the catalogue arrived (`taos-store --app <id>`): fetched when it does
        return;
    }
    if (m_cat.entry(id).value(u"kind"_s).toString() == u"webapp"_s)
        return;
    client()->http(u"GET"_s, u"/api/store/app/"_s + QString::fromUtf8(QUrl::toPercentEncoding(id)))->then(this, [this, id](const Taos::Result &r) {
        if (!r.ok())
            return;
        m_detail.insert(id, r.value.toObject().toVariantMap());
        bump();
    });
}

QVariantMap StoreEngine::app(const QString &id) const
{
    QVariantMap e = m_cat.entry(id);
    if (e.isEmpty())
        return e;
    const QVariantMap d = m_detail.value(id);
    if (!d.isEmpty()) {
        e[u"homepage"_s] = d.value(u"homepage"_s).toString();
        e[u"capabilities"_s] = d.value(u"capabilities"_s);
        e[u"configCount"_s] = d.value(u"config_schema"_s).toList().size();
        e[u"detailLoaded"_s] = true;
    }
    if (e.value(u"kind"_s).toString() == u"webapp"_s) {
        QVariantList mine;
        for (const QVariant &v : m_web->instances())
            if (v.toMap().value(u"source"_s).toString() == id)
                mine << v;
        e[u"instanceRows"_s] = mine;
        e[u"homepage"_s] = e.value(u"url"_s);
    }
    if (m_modelRows.contains(id)) {
        QVariantList vs;
        for (const QJsonValue &v : m_modelRows.value(id).value(u"variants"_s).toArray()) {
            const QJsonObject o = v.toObject();
            const QString fit = o.value(u"compatibility"_s).toString();
            vs << QVariantMap{{u"id"_s, o.value(u"id"_s).toString()},
                              {u"name"_s, o.value(u"name"_s).toString(o.value(u"id"_s).toString())},
                              {u"sizeText"_s, sizeText(o.value(u"size_mb"_s).toDouble())},
                              {u"fit"_s, fit}, {u"fitText"_s, fitText(fit == u"green"_s ? u"green"_s : fit == u"yellow"_s ? u"yellow"_s : fit.isEmpty() ? QString() : u"red"_s)},
                              {u"downloaded"_s, o.value(u"downloaded"_s).toBool()}};
        }
        if (!vs.isEmpty())
            e[u"modelVariants"_s] = vs;
    }
    const QVariantMap rt = m_runtime.value(id);
    if (!rt.isEmpty()) {
        e[u"runtimeHost"_s] = rt.value(u"runtime_host"_s);
        e[u"runtimePort"_s] = rt.value(u"runtime_port"_s);
    }
    e[u"installedVersion"_s] = rt.value(u"version"_s).toString();
    return e;
}

QVariantList StoreEngine::asks(const QString &id) const
{
    const QVariantMap e = app(id);
    QVariantList rows;
    auto row = [&](const QString &label, const QString &value) {
        if (!value.isEmpty())
            rows << QVariantMap{{u"label"_s, label}, {u"value"_s, value}};
    };
    if (e.isEmpty())
        return rows;
    if (e.value(u"kind"_s).toString() == u"webapp"_s) {
        row(tr("Opens"), e.value(u"url"_s).toString());
        row(tr("Storage"), tr("Its own private storage and sign-in for every copy you add"));
        row(tr("Runs in"), tr("Its own browser window"));
        return rows;
    }
    if (e.value(u"ramMb"_s).toInt() > 0)
        row(tr("Memory"), sizeText(e.value(u"ramMb"_s).toInt()));
    if (e.value(u"diskMb"_s).toInt() > 0)
        row(tr("Storage"), sizeText(e.value(u"diskMb"_s).toInt()));
    else if (e.value(u"kind"_s).toString() == u"model"_s)
        row(tr("Download"), e.value(u"sizeText"_s).toString());
    row(tr("Runs in"), e.value(u"runtimeText"_s).toString());
    row(tr("This device"), e.value(u"fitText"_s).toString());
    row(tr("Licence"), e.value(u"license"_s).toString());
    if (e.value(u"licenseClass"_s).toString() == u"non-commercial"_s)
        row(tr("Model weights"), tr("%1, for non-commercial use only. You are asked to agree before installing.").arg(e.value(u"weightsLicense"_s).toString()));
    if (e.value(u"configCount"_s).toInt() > 0)
        row(tr("Settings"), tr("%1 options to set after installing").arg(e.value(u"configCount"_s).toInt()));
    return rows;
}

QVariantList StoreEngine::installedRows() const
{
    QVariantList out;
    static const QHash<QString, QString> kindLabel = {{u"app"_s, tr("App")}, {u"agent"_s, tr("Agent framework")}, {u"model"_s, tr("Model")},
                                                      {u"plugin"_s, tr("Plugin")}, {u"mcp"_s, tr("Tool server")}};
    for (const QVariant &v : m_cat.section(u"installed"_s)) {
        const QVariantMap e = v.toMap();
        if (e.value(u"kind"_s).toString() == u"webapp"_s)
            continue;
        QStringList sub;
        const QString ver = m_runtime.value(e.value(u"id"_s).toString()).value(u"version"_s).toString();
        sub << (ver.isEmpty() ? e.value(u"version"_s).toString() : ver);
        sub << e.value(u"runtimeText"_s).toString();
        sub.removeAll(QString());
        out << QVariantMap{{u"id"_s, e.value(u"id"_s)}, {u"name"_s, e.value(u"name"_s)}, {u"kind"_s, e.value(u"kind"_s)},
                           {u"kindLabel"_s, kindLabel.value(e.value(u"kind"_s).toString(), tr("App"))},
                           {u"subtitle"_s, sub.join(u" · "_s)}, {u"letter"_s, e.value(u"letter"_s)}, {u"source"_s, u"catalog"_s},
                           {u"updateAvailable"_s, e.value(u"updateAvailable"_s)}};
    }
    for (const QVariant &v : m_web->instances()) {
        const QVariantMap m = v.toMap();
        out << QVariantMap{{u"id"_s, m.value(u"id"_s)}, {u"name"_s, m.value(u"name"_s)}, {u"kind"_s, u"webapp"_s}, {u"kindLabel"_s, tr("Web app")},
                           {u"subtitle"_s, m.value(u"host"_s).toString() + u" · "_s + m.value(u"sizeText"_s).toString()},
                           {u"letter"_s, m.value(u"letter"_s)}, {u"source"_s, u"webapp"_s}, {u"iconUrl"_s, m.value(u"iconUrl"_s)},
                           {u"running"_s, m.value(u"running"_s)}};
    }
    for (const QVariant &v : m_themes->themes()) {
        const QVariantMap m = v.toMap();
        out << QVariantMap{{u"id"_s, m.value(u"id"_s)}, {u"name"_s, m.value(u"name"_s)}, {u"kind"_s, u"theme"_s}, {u"kindLabel"_s, tr("Theme")},
                           {u"subtitle"_s, m.value(u"origin"_s).toString() + (m.value(u"current"_s).toBool() ? tr(" · In use") : QString())},
                           {u"letter"_s, m.value(u"name"_s).toString().left(1)}, {u"source"_s, u"theme"_s}};
    }
    return out;
}

// ---------------------------------------------------------------- install state machine

QVariantMap StoreEngine::installState(const QString &id) const
{
    const Inst s = m_inst.value(id);
    return QVariantMap{{u"state"_s, s.state}, {u"text"_s, s.text}, {u"detail"_s, s.detail}, {u"percent"_s, s.percent},
                       {u"consent"_s, s.consent}, {u"consentText"_s, s.consentText}, {u"error"_s, s.error}};
}

QString StoreEngine::refusal(const Taos::Error &e, const QString &verb) const
{
    switch (e.kind()) {
    case Taos::Error::Forbidden:
        // An administrator can be refused too (a manifest that failed its signature check): then only the controller's
        // sentence is true. A member is told what the route needs.
        if (isAdmin())
            return e.message().isEmpty() ? tr("The controller refused this.") : e.message();
        return tr("%1 needs an administrator account. Sign in as an administrator to do this. The controller said: %2")
            .arg(verb, e.message().isEmpty() ? tr("forbidden") : e.message());
    case Taos::Error::Unauthenticated:
        return tr("Your session has ended. Sign in again and retry.");
    case Taos::Error::Network:
        return tr("Can't reach the controller.");
    default:
        return e.message().isEmpty() ? e.userMessage() : e.message();   // the controller's own words
    }
}

void StoreEngine::install(const QString &id, const QString &variantId)
{
    const QVariantMap e = m_cat.entry(id);
    if (e.isEmpty()) {
        Q_EMIT toast(tr("That app is not in the catalogue."));
        return;
    }
    if (e.value(u"kind"_s).toString() == u"webapp"_s) {
        installWebapp(id);
        return;
    }
    Inst &s = m_inst[id];
    if (s.state == u"installing"_s || s.state == u"removing"_s)
        return;
    s = Inst();
    s.variant = variantId;
    // Docker is a need the person should see before something is pulled and started: say so and ask first.
    if (e.value(u"needsDocker"_s).toBool()) {
        s.state = u"consent"_s;
        s.consent = u"docker"_s;
        s.consentText = tr("%1 runs in a Docker container. Docker must be installed on the controller, and the app will be downloaded and started there.")
                            .arg(e.value(u"name"_s).toString());
        bump();
        return;
    }
    postInstall(id);
}

void StoreEngine::acceptConsent(const QString &id)
{
    if (!m_inst.contains(id) || m_inst[id].state != u"consent"_s)
        return;
    Inst &s = m_inst[id];
    if (s.consent == u"docker"_s)
        s.dockerOk = true;
    else if (s.consent == u"license"_s)
        s.licenseOk = true;
    s.consent.clear();
    s.consentText.clear();
    postInstall(id);
}

void StoreEngine::cancelConsent(const QString &id)
{
    if (m_inst.contains(id) && m_inst[id].state == u"consent"_s) {
        m_inst.remove(id);
        bump();
    }
}

void StoreEngine::dismiss(const QString &id)
{
    if (m_inst.contains(id) && m_inst[id].state != u"installing"_s && m_inst[id].state != u"removing"_s) {
        m_inst.remove(id);
        bump();
    }
}

void StoreEngine::postInstall(const QString &id)
{
    Taos::Client *c = client();
    Inst &s = m_inst[id];
    s.state = u"installing"_s;
    s.text = tr("Installing");
    s.detail.clear();
    s.error.clear();
    s.percent = -1;
    QJsonObject body{{u"app_id"_s, id}, {u"target_remote"_s, u"local"_s}};
    if (!s.variant.isEmpty())
        body[u"variant_id"_s] = s.variant;
    if (s.licenseOk)
        body[u"accepted"_s] = true;
    m_poll.start();
    bump();
    Taos::Reply *rep = c->http(u"POST"_s, u"/api/store/install-v2"_s, {}, body);
    s.reply = rep;
    rep->then(this, [this, id](const Taos::Result &r) {
        if (!m_inst.contains(id))
            return;
        Inst &s = m_inst[id];
        if (r.ok()) {
            finishInstall(id, true, tr("%1 is installed.").arg(m_cat.entry(id).value(u"name"_s).toString()));
        } else if (r.error.httpStatus() == 412) {
            // non-commercial weights: the same words the controller uses (store_install.py:1033)
            const QVariantMap e = m_cat.entry(id);
            s.state = u"consent"_s;
            s.consent = u"license"_s;
            s.consentText = tr("%1 downloads model weights licensed under %2, for non-commercial use only. Continuing means you agree to that license for the weights this service uses.")
                                .arg(e.value(u"name"_s).toString(), e.value(u"weightsLicense"_s).toString());
            bump();
        } else if (r.error.kind() == Taos::Error::Network || r.error.kind() == Taos::Error::Cancelled) {
            // The controller may be still working: the progress route decides (see pollProgress).
            s.text = tr("Waiting for the controller");
            s.error = u"lost"_s;
            bump();
        } else {
            finishInstall(id, false, refusal(r.error, tr("Installing")));
        }
    });
}

void StoreEngine::pollProgress()
{
    bool any = false;
    for (auto it = m_inst.begin(); it != m_inst.end(); ++it) {
        if (it->state != u"installing"_s)
            continue;
        any = true;
        const QString id = it.key();
        client()->http(u"GET"_s, u"/api/store/install-progress/by-app/"_s + QString::fromUtf8(QUrl::toPercentEncoding(id)))
            ->then(this, [this, id](const Taos::Result &r) {
                if (!m_inst.contains(id) || m_inst[id].state != u"installing"_s)
                    return;
                Inst &s = m_inst[id];
                if (!r.ok()) {
                    if (s.error == u"lost"_s && r.error.kind() == Taos::Error::Network) {
                        finishInstall(id, false, tr("Lost contact with the controller. Check whether the install finished, then try again."));
                    }
                    return;
                }
                const QJsonObject a = r.value.toObject().value(u"active"_s).toObject();
                if (a.isEmpty()) {
                    if (s.error == u"lost"_s)
                        finishInstall(id, false, tr("Lost contact with the controller. Check whether the install finished, then try again."));
                    return;
                }
                const QString st = a.value(u"state"_s).toString();
                const double total = a.value(u"bytes_total"_s).toDouble();
                if (!a.value(u"percent"_s).isNull())
                    s.percent = a.value(u"percent"_s).toDouble();
                else if (total > 0)
                    s.percent = a.value(u"bytes_downloaded"_s).toDouble() * 100.0 / total;
                s.detail = a.value(u"detail"_s).toString();
                if (!st.isEmpty())
                    s.text = st == u"downloading"_s ? tr("Downloading") : st == u"unpacking"_s ? tr("Installing") : s.text;
                if (s.error == u"lost"_s) {
                    if (st == u"installed"_s)
                        finishInstall(id, true, tr("%1 is installed.").arg(m_cat.entry(id).value(u"name"_s).toString()));
                    else if (st == u"failed"_s || st == u"cancelled"_s)
                        finishInstall(id, false, a.value(u"error"_s).toString(tr("The install failed.")));
                }
                bump();
            });
    }
    if (!any)
        m_poll.stop();
}

void StoreEngine::finishInstall(const QString &id, bool ok, const QString &text)
{
    Inst &s = m_inst[id];
    s.state = ok ? u"installed"_s : u"failed"_s;
    s.text = ok ? tr("Installed") : tr("Failed");
    s.error = ok ? QString() : text;
    s.percent = ok ? 100 : -1;
    if (ok) {
        m_cat.markInstalled(id, true);
        fetchInstalled();
        fetchModels();
    }
    bump();
    Q_EMIT installFinished(id, ok, text);
    Q_EMIT toast(text);
}

void StoreEngine::remove(const QString &id)
{
    const QVariantMap e = m_cat.entry(id);
    if (e.isEmpty() || e.value(u"kind"_s).toString() == u"webapp"_s)
        return;
    Inst &s = m_inst[id];
    if (s.state == u"installing"_s || s.state == u"removing"_s)
        return;
    s = Inst();
    s.state = u"removing"_s;
    s.text = tr("Removing");
    bump();
    client()->http(u"POST"_s, u"/api/store/uninstall"_s, {}, QJsonObject{{u"app_id"_s, id}})->then(this, [this, id](const Taos::Result &r) {
        Inst &s = m_inst[id];
        if (r.ok()) {
            m_cat.markInstalled(id, false);
            s = Inst();
            fetchInstalled();
            bump();
            Q_EMIT installFinished(id, true, tr("Removed."));
            Q_EMIT toast(tr("%1 is removed.").arg(m_cat.entry(id).value(u"name"_s).toString()));
        } else {
            s.state = u"failed"_s;
            s.text = tr("Failed");
            s.error = r.error.httpStatus() == 404 ? tr("The controller says it is not installed.") : refusal(r.error, tr("Removing"));
            bump();
            Q_EMIT installFinished(id, false, s.error);
        }
    });
}

bool StoreEngine::open(const QString &id)
{
    const QVariantMap e = app(id);
    if (e.isEmpty())
        return false;
    if (e.value(u"kind"_s).toString() == u"agent"_s) {
        if (QProcess::startDetached(u"taos-agents"_s, {}))
            return true;
        Q_EMIT toast(tr("The Agents app is not installed."));
        return false;
    }
    const QString host = e.value(u"runtimeHost"_s).toString();
    const int port = e.value(u"runtimePort"_s).toInt();
    if (!host.isEmpty() && port > 0) {
        QUrl u;
        u.setScheme(u"http"_s);
        u.setHost(host);
        u.setPort(port);
        if (QDesktopServices::openUrl(u))
            return true;
        Q_EMIT toast(tr("Could not open %1.").arg(u.toString()));
        return false;
    }
    Q_EMIT toast(tr("%1 has no window to open from here.").arg(e.value(u"name"_s).toString()));
    return false;
}

// ---------------------------------------------------------------- web apps

void StoreEngine::installWebapp(const QString &storeId)
{
    const QVariantMap e = m_cat.entry(storeId);
    if (e.isEmpty())
        return;
    QString name = e.value(u"name"_s).toString();
    const int have = m_web->instanceCounts().value(storeId);
    if (have > 0)
        name = tr("%1 (%2)").arg(name).arg(have + 1);
    m_web->add(e.value(u"url"_s).toString(), name, storeId, true, false);
}

void StoreEngine::addWebsite(const QString &url, const QString &name, bool autoIcon)
{
    m_web->add(url, name, QString(), autoIcon, false);
}

// ---------------------------------------------------------------- QML

void registerStoreQmlTypes()
{
    qmlRegisterType<StoreEngine>("Taos.Store", 1, 0, "StoreEngine");
    qmlRegisterType<AppsOverview>("Taos.Store", 1, 0, "AppsOverview");
    qmlRegisterUncreatableType<WebAppsService>("Taos.Store", 1, 0, "WebAppsService", u"owned by StoreEngine"_s);
    qmlRegisterUncreatableType<ThemesService>("Taos.Store", 1, 0, "ThemesService", u"owned by StoreEngine"_s);
}

} // namespace Taos::Store
