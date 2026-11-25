// SPDX-License-Identifier: AGPL-3.0-or-later
// The Store's engine against the fake controller (fixtures in the shapes of the real routes) and a fake browser:
// catalogue normalisation and merge, search, sections, the install state machine (403, the Docker and licence
// consents, progress, removal), models, themes, web apps end to end through the web apps engine, and the
// Settings > Apps overview.
#include <QSignalSpy>
#include <QTemporaryDir>

#include "apphost.h"
#include "appsoverview.h"
#include "storeengine.h"

using namespace Qt::StringLiterals;
using namespace Taos::Store;

namespace {

QJsonArray builtinWebapps()
{
    QFile f(QStringLiteral(WEBAPPS_JSON));
    f.open(QIODevice::ReadOnly);
    return QJsonDocument::fromJson(f.readAll()).object().value(u"apps"_s).toArray();
}

// JSON with single quotes (moc cannot read raw strings that hold a URL), as bytes
QByteArray jq(const char *text) { return QByteArray(text).replace('\'', '"'); }

QStringList ids(const QVariantList &l)
{
    QStringList out;
    for (const QVariant &v : l)
        out << v.toMap().value(u"id"_s).toString();
    return out;
}

QJsonObject server(const QString &id, const QString &type, const QJsonObject &extra = {})
{
    QJsonObject o{{u"id"_s, id}, {u"name"_s, id.left(1).toUpper() + id.mid(1)}, {u"type"_s, type}, {u"version"_s, u"1.0"_s},
                  {u"description"_s, u"desc of "_s + id}};
    for (auto it = extra.begin(); it != extra.end(); ++it)
        o.insert(it.key(), it.value());
    return o;
}

QJsonObject artIndex()
{
    return QJsonDocument::fromJson(R"({
      "icons": {"gitea": "gitea", "youtube": "webapp-youtube"},
      "iconExt": {"gitea": "png", "webapp-youtube": "svg", "qwen": "png"},
      "families": [{"prefix": "qwen", "icon": "qwen"}],
      "covers": ["gitea", "stable-diffusion"],
      "coverAlias": {"sd-webui": "stable-diffusion"}})").object();
}

void writeFile(const QString &p, const QByteArray &d)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    f.open(QIODevice::WriteOnly);
    f.write(d);
}

} // namespace

class TstStoreLogic : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    StoreEngine *m_e = nullptr;
    QTemporaryDir m_tmp;
    QString m_data, m_stop;

    TaosWebapp::Options webOpts()
    {
        TaosWebapp::Options o;
        o.root = m_data + u"/taos/webapps"_s;
        o.applicationsDir = m_data + u"/applications"_s;
        o.fetcher = [](const QUrl &) { return TaosWebapp::FetchResult(); };   // no network
        return o;
    }
    ThemesService::Paths themePaths()
    {
        ThemesService::Paths p;
        p.useDefaults = false;
        p.watch = false;
        p.appearancePath = m_tmp.path() + u"/config/appearance.json"_s;
        p.mirrorDir = m_tmp.path() + u"/mirror"_s;
        p.themeDirs = {m_tmp.path() + u"/themes"_s};
        return p;
    }
    void start(bool admin = false, int catalogFail = 0)
    {
        delete m_e;
        m_e = nullptr;
        delete m_host;
        m_host = nullptr;
        m_data = m_tmp.path() + u'/' + QString::number(QRandomGenerator::global()->generate());
        QDir().mkpath(m_data);
        m_fake->config({{u"store"_s, u"seed"_s}, {u"admin"_s, admin}, {u"outcome"_s, QJsonObject()}, {u"install_delay_ms"_s, 0},
                        {u"catalog_fail"_s, catalogFail}});
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.store"_s);
        QVERIFY(m_host->waitSignedIn());
        m_e = new StoreEngine;
        m_e->setWebappOptions(webOpts());
        m_e->setThemePaths(themePaths());
        m_e->setBuiltinWebapps(builtinWebapps());
        m_e->setSession(&m_host->session());
        m_e->setRunning(true);
    }
    void loaded() { QVERIFY(waitFor([this] { return m_e->loaded() && m_e->profileId() == u"cpu-only"_s && !m_e->app(u"qwen3-8b"_s).value(u"fit"_s).toString().isEmpty(); })); }
    QString stateOf(const QString &id) { return m_e->installState(id).value(u"state"_s).toString(); }

private Q_SLOTS:
    void initTestCase()
    {
        QVERIFY(m_tmp.isValid());
        qputenv("HOME", m_tmp.path().toUtf8());
        qputenv("XDG_DATA_HOME", (m_tmp.path() + u"/xdg-data"_s).toUtf8());
        qputenv("XDG_CONFIG_HOME", (m_tmp.path() + u"/xdg-config"_s).toUtf8());
        qputenv("TAOS_APPEARANCE_MIRROR_DIR", (m_tmp.path() + u"/mirror"_s).toUtf8());   // the greeter's copy of a person's theme
        qputenv("TAOS_SYSTEM_APPEARANCE", (m_tmp.path() + u"/etc-appearance.json"_s).toUtf8());
        qputenv("TAOS_SYSTEM_THEME_DIR", (m_tmp.path() + u"/system-themes"_s).toUtf8());
        qunsetenv("TAOS_APPEARANCE");
        qunsetenv("TAOS_THEME_DIR");
        qunsetenv("TAOS_THEME");
        qunsetenv("TAOS_THEME_VARIANT");
        qputenv("TAOS_WEBAPP_BROWSER", QByteArray(FAKE_BROWSER));
        // no container apps engine unless a test brings its own: a taos-appgroups on this machine's PATH is not a fixture
        qputenv("TAOS_APPGROUPS_BIN", "/nonexistent/taos-appgroups");
        m_stop = m_tmp.path() + u"/stop"_s;
        qputenv("FAKE_BROWSER_STOP", m_stop.toUtf8());
        qputenv("FAKE_BROWSER_LOG", (m_tmp.path() + u"/browser.log"_s).toUtf8());
        m_fake = new FakeHost;
        QVERIFY(m_fake->ok());
    }
    void cleanupTestCase()
    {
        writeFile(m_stop, "stop");
        delete m_e;
        delete m_host;
        delete m_fake;
    }
    void cleanup() { writeFile(m_stop, "stop"); QTest::qWait(300); QFile::remove(m_stop); }

    // ---- catalogue logic, no network ---------------------------------------------------------------------
    void server_entries_are_normalised_to_the_shapes_the_routes_return()
    {
        Catalog c;
        c.setProfile(u"cpu-only"_s);
        c.setServer(QJsonArray{
            server(u"searxng"_s, u"service"_s, {{u"install_method"_s, u"docker"_s}, {u"category"_s, u"infrastructure"_s},
                                               {u"requires"_s, QJsonObject{{u"ram_mb"_s, 128}, {u"disk_mb"_s, 200}}},
                                               {u"hardware_tiers"_s, QJsonObject{{u"cpu-only"_s, u"full"_s}}}, {u"stars"_s, 15000}}),
            server(u"n8n"_s, u"service"_s, {{u"install_method"_s, u"docker"_s}, {u"runtime"_s, u"either"_s}}),   // a runtime field, tolerated
            server(u"old"_s, u"service"_s, {{u"install_method"_s, u"lxc"_s}}),                                      // and absent
            server(u"flux"_s, u"model"_s, {{u"hardware_tiers"_s, QJsonObject{{u"arm"_s, u"full"_s}}},
                                          {u"variants"_s, QJsonArray{QJsonObject{{u"id"_s, u"q4"_s}, {u"size_mb"_s, 12000}}, QJsonObject{{u"id"_s, u"q2"_s}, {u"size_mb"_s, 6000}}}}}),
            server(u"mus"_s, u"service"_s, {{u"license_class"_s, u"non-commercial"_s}, {u"weights_license"_s, u"CC-BY-NC 4.0"_s}}),
            server(u"oc"_s, u"agent-framework"_s), server(u"p"_s, u"plugin"_s), server(u"m"_s, u"mcp"_s),
            server(u"upd"_s, u"service"_s, {{u"installed"_s, true}, {u"update_available"_s, true}}),
            server(u"gone"_s, u"service"_s, {{u"installed"_s, false}, {u"update_available"_s, true}})});
        QVariantMap e = c.entry(u"searxng"_s);
        QCOMPARE(e[u"kind"_s].toString(), u"app"_s);
        QCOMPARE(e[u"section"_s].toString(), u"apps"_s);
        QVERIFY(e[u"needsDocker"_s].toBool());
        QCOMPARE(e[u"runtimeText"_s].toString(), u"Needs Docker"_s);
        QCOMPARE(e[u"ramMb"_s].toInt(), 128);
        QCOMPARE(e[u"sizeText"_s].toString(), u"200 MB"_s);
        QCOMPARE(e[u"fit"_s].toString(), u"green"_s);
        QVERIFY(!c.entry(u"n8n"_s)[u"needsDocker"_s].toBool());                      // runtime: either
        QCOMPARE(c.entry(u"n8n"_s)[u"runtimeText"_s].toString(), u"Docker or LXC container"_s);
        QVERIFY(!c.entry(u"old"_s)[u"needsDocker"_s].toBool());
        QCOMPARE(c.entry(u"old"_s)[u"runtimeText"_s].toString(), u"LXC container"_s);
        e = c.entry(u"flux"_s);
        QCOMPARE(e[u"kind"_s].toString(), u"model"_s);
        QCOMPARE(e[u"fit"_s].toString(), u"red"_s);                                  // no tier for this profile: unsupported
        QCOMPARE(e[u"sizeText"_s].toString(), u"5.9 GB"_s);                          // the smallest variant
        QCOMPARE(e[u"variants"_s].toList().size(), 2);
        QCOMPARE(c.entry(u"mus"_s)[u"licenseClass"_s].toString(), u"non-commercial"_s);
        QCOMPARE(c.entry(u"oc"_s)[u"kind"_s].toString(), u"agent"_s);
        QCOMPARE(c.entry(u"p"_s)[u"section"_s].toString(), u"plugins"_s);
        QCOMPARE(c.entry(u"m"_s)[u"section"_s].toString(), u"plugins"_s);
        QVERIFY(c.entry(u"upd"_s)[u"updateAvailable"_s].toBool());
        QVERIFY(!c.entry(u"gone"_s)[u"updateAvailable"_s].toBool());                 // an update only counts for what is installed
        QCOMPARE(ids(c.section(u"updates"_s)), (QStringList{u"Upd"_s.toLower()}));
        QCOMPARE(ids(c.section(u"installed"_s)), (QStringList{u"upd"_s}));
        // a model's fit comes from the controller's own judgement once /api/models has answered
        c.setModelFit(QJsonArray{QJsonObject{{u"id"_s, u"flux"_s}, {u"compatibility"_s, u"yellow"_s}}});
        QCOMPARE(c.entry(u"flux"_s)[u"fit"_s].toString(), u"yellow"_s);
        // garbage in, no crash
        Catalog bad;
        bad.setServer(QJsonArray{QJsonValue(5), QJsonObject(), QJsonObject{{u"id"_s, u"x"_s}}});
        QCOMPARE(bad.size(), 1);
        QCOMPARE(bad.entry(u"x"_s)[u"name"_s].toString(), u"x"_s);
    }


    // ---- container apps: group choice, licence, create group, move, update ---------------------------------------
    void container_apps_ask_for_a_group_and_the_licence_before_installing()
    {
        QTemporaryDir ag;
        QVERIFY(ag.isValid());
        const QString d = ag.path();
        writeFile(d + u"/catalog.json"_s, jq("{'ok':true,'apps':[ {'id':'claude-desktop','name':'Claude','type':'container-desktop-app','description':'d','publisher':'Anthropic','homepage':'https://x.example/', 'licence':'Anthropic Consumer Terms of Service','licence_url':'https://www.anthropic.com/legal/consumer-terms','licence_required':true, 'terms_note':'Proprietary','version':'apt','categories':['Development'],'can_update':true,'window_app_id':'claude-desktop','grants':{}}, {'id':'chromium','name':'Chromium','type':'container-desktop-app','description':'d','publisher':'p','homepage':'https://x.example/', 'licence':'BSD','licence_required':false,'terms_note':'Open','version':'debian','categories':['Network'],'window_app_id':'chromium','grants':{}}]}"));
        writeFile(d + u"/list.json"_s, jq("{'ok':true,'apps':[{'id':'chromium','name':'Chromium','group':'default','installed_version':'140'}]}"));
        writeFile(d + u"/groups.json"_s, jq("{'ok':true,'groups':[ {'name':'dev-apps','display_name':'Dev apps','state':'running','apps':[]}, {'name':'default','display_name':'My apps','state':'running','apps':['chromium']}]}"));
        qputenv("TAOS_APPGROUPS_BIN", QByteArray(FAKE_APPGROUPS));
        qputenv("FAKE_AG_DIR", d.toUtf8());
        start();
        loaded();
        QVERIFY(waitFor([this] { return !m_e->app(u"claude-desktop"_s).isEmpty(); }));
        const auto log = [&] { QFile f(d + u"/args.log"_s); return f.open(QIODevice::ReadOnly) ? QString::fromUtf8(f.readAll()) : QString(); };

        // 1. Get asks first: nothing is installed yet, the licence and the group are on the consent card
        QCOMPARE(m_e->app(u"claude-desktop"_s).value(u"licenceRequired"_s).toBool(), true);
        QCOMPARE(m_e->app(u"claude-desktop"_s).value(u"licenceUrl"_s).toString(), u"https://www.anthropic.com/legal/consumer-terms"_s);
        m_e->install(u"claude-desktop"_s);
        QCOMPARE(stateOf(u"claude-desktop"_s), u"consent"_s);
        QCOMPARE(m_e->installState(u"claude-desktop"_s).value(u"consent"_s).toString(), u"containerapp"_s);
        QVERIFY(m_e->installState(u"claude-desktop"_s).value(u"consentText"_s).toString().contains(u"proprietary"_s));
        QVERIFY2(!log().contains(u"install claude-desktop"_s), "nothing is installed before the person agrees");
        m_e->refreshGroups();
        QCOMPARE(m_e->appGroups().size(), 2);
        // the engine lists dev-apps before the default group (as Incus does); the Store puts "My apps" first
        QCOMPARE(m_e->appGroups()[0].toMap().value(u"name"_s).toString(), u"default"_s);
        QCOMPARE(m_e->appGroups()[1].toMap().value(u"displayName"_s).toString(), u"Dev apps"_s);
        // 2. a group chosen, licence accepted: the engine is asked with that group and the licence flag
        m_e->chooseGroup(u"claude-desktop"_s, u"dev-apps"_s);
        m_e->acceptConsent(u"claude-desktop"_s);
        QVERIFY(waitFor([&] { return stateOf(u"claude-desktop"_s) == u"installed"_s; }));
        QVERIFY2(log().contains(u"install claude-desktop --json --target group:dev-apps --accept-licence claude-desktop --accepted-by "_s), qPrintable(log()));
        // 3. an open source app is asked about the group too, with no licence flag
        m_e->install(u"chromium"_s);
        QCOMPARE(stateOf(u"chromium"_s), u"consent"_s);
        QVERIFY(m_e->installState(u"chromium"_s).value(u"consentText"_s).toString().contains(u"Choose the app group"_s));
        m_e->cancelConsent(u"chromium"_s);
        QCOMPARE(stateOf(u"chromium"_s), u"idle"_s);
        qputenv("TAOS_APPGROUPS_BIN", "/nonexistent/taos-appgroups");
    }
    void container_apps_make_a_group_and_move_into_it()
    {
        QTemporaryDir ag;
        const QString d = ag.path();
        writeFile(d + u"/catalog.json"_s, jq("{'ok':true,'apps':[{'id':'chromium','name':'Chromium','type':'container-desktop-app','description':'d','publisher':'p','homepage':'https://x.example/','licence':'BSD','terms_note':'Open','version':'debian','categories':['Network'],'window_app_id':'chromium','grants':{}}]}"));
        writeFile(d + u"/list.json"_s, jq("{'ok':true,'apps':[{'id':'chromium','name':'Chromium','group':'default','installed_version':'140'}]}"));
        writeFile(d + u"/groups.json"_s, jq("{'ok':true,'groups':[{'name':'default','display_name':'My apps','state':'running','apps':['chromium']}]}"));
        qputenv("TAOS_APPGROUPS_BIN", QByteArray(FAKE_APPGROUPS));
        qputenv("FAKE_AG_DIR", d.toUtf8());
        qunsetenv("FAKE_AG_FAIL");
        start();
        loaded();
        QVERIFY(waitFor([this] { return m_e->app(u"chromium"_s)[u"installed"_s].toBool(); }));
        const auto log = [&] { QFile f(d + u"/args.log"_s); return f.open(QIODevice::ReadOnly) ? QString::fromUtf8(f.readAll()) : QString(); };
        QCOMPARE(m_e->app(u"chromium"_s)[u"groupName"_s].toString(), u"My apps"_s);
        QCOMPARE(m_e->groupSlug(u"Dev apps"_s), u"dev-apps"_s);
        QCOMPARE(m_e->groupSlug(u"  Internet   apps "_s), u"internet-apps"_s);
        QCOMPARE(m_e->groupSlug(u"1 apps"_s), QString());
        QCOMPARE(m_e->groupSlug(u"default"_s), QString());
        QCOMPARE(m_e->groupSlug(QString(QChar(0x2603))), QString());
        // a new group: the engine is asked for the slug and the typed name; the list is read again afterwards
        writeFile(d + u"/groups.json"_s, jq("{'ok':true,'groups':[{'name':'default','display_name':'My apps','state':'running','apps':['chromium']},{'name':'internet-apps','display_name':'Internet apps','state':'running','apps':[]}]}"));
        m_e->createAppGroup(u"Internet apps"_s);
        QCOMPARE(m_e->groupWork().value(u"state"_s).toString(), u"creating"_s);
        QVERIFY(waitFor([this] { return m_e->groupWork().value(u"created"_s).toBool(); }));
        QVERIFY2(log().contains(u"group create internet-apps --display-name Internet apps --json"_s), qPrintable(log()));
        QCOMPARE(m_e->appGroups().size(), 2);
        m_e->createAppGroup(u"???"_s);
        QCOMPARE(m_e->groupWork().value(u"state"_s).toString(), u"failed"_s);
        // move: asked with the group; when it ends the app is listed in its new group
        writeFile(d + u"/list.json"_s, jq("{'ok':true,'apps':[{'id':'chromium','name':'Chromium','group':'internet-apps','installed_version':'140'}]}"));
        m_e->moveContainerApp(u"chromium"_s, u"internet-apps"_s);
        QCOMPARE(stateOf(u"chromium"_s), u"moving"_s);
        QVERIFY(waitFor([&] { return stateOf(u"chromium"_s) == u"idle"_s; }));
        QVERIFY2(log().contains(u"move chromium --to group:internet-apps --json"_s), qPrintable(log()));
        QCOMPARE(m_e->app(u"chromium"_s)[u"groupName"_s].toString(), u"Internet apps"_s);
        // the engine refuses (the app is open): the person gets its sentence, and it is not offered as a retry of an install
        qputenv("FAKE_AG_FAIL", "1");
        m_e->moveContainerApp(u"chromium"_s, u"default"_s);
        QVERIFY(waitFor([&] { return stateOf(u"chromium"_s) == u"failed"_s; }));
        QCOMPARE(m_e->installState(u"chromium"_s).value(u"op"_s).toString(), u"move"_s);
        QVERIFY(m_e->installState(u"chromium"_s).value(u"error"_s).toString().contains(u"Close it"_s));
        qunsetenv("FAKE_AG_FAIL");
        m_e->dismiss(u"chromium"_s);
        QCOMPARE(stateOf(u"chromium"_s), u"idle"_s);
        qputenv("TAOS_APPGROUPS_BIN", "/nonexistent/taos-appgroups");
    }

    void builtin_web_apps_merge_with_the_server_catalogue()
    {
        Catalog c;
        c.setBuiltinWebapps(builtinWebapps());
        QVERIFY(c.has(u"youtube"_s));
        const QVariantList web = c.section(u"webapps"_s);
        QVERIFY(web.size() >= 6 && web.size() <= 9);
        for (const QString &id : {u"youtube"_s, u"youtube-music"_s, u"whatsapp-web"_s, u"telegram-web"_s, u"discord"_s, u"x"_s, u"github"_s, u"google-maps"_s})
            QVERIFY2(c.has(id), qPrintable(id));
        const QVariantMap yt = c.entry(u"youtube"_s);
        QCOMPARE(yt[u"kind"_s].toString(), u"webapp"_s);
        QCOMPARE(yt[u"url"_s].toString(), u"https://www.youtube.com"_s);
        QCOMPARE(yt[u"host"_s].toString(), u"www.youtube.com"_s);
        QVERIFY(!yt[u"needsDocker"_s].toBool());
        // a server entry of type webapp with the same id wins; others are added
        c.setServer(QJsonArray{server(u"youtube"_s, u"webapp"_s, {{u"url"_s, u"https://m.youtube.com"_s}, {u"name"_s, u"YouTube (server)"_s}}),
                               server(u"jellyfin"_s, u"webapp"_s, {{u"url"_s, u"https://jf.example"_s}}), server(u"gitea"_s, u"service"_s)});
        QCOMPARE(c.entry(u"youtube"_s)[u"url"_s].toString(), u"https://m.youtube.com"_s);
        QCOMPARE(c.entry(u"youtube"_s)[u"name"_s].toString(), u"YouTube (server)"_s);
        QVERIFY(ids(c.section(u"webapps"_s)).contains(u"jellyfin"_s));
        QVERIFY(!ids(c.section(u"webapps"_s)).contains(u"gitea"_s));
        // instances mark an entry added
        c.setInstanceCounts({{u"discord"_s, 2}});
        QVERIFY(c.entry(u"discord"_s)[u"installed"_s].toBool());
        QCOMPARE(c.entry(u"discord"_s)[u"instances"_s].toInt(), 2);
        QVERIFY(!c.entry(u"x"_s)[u"installed"_s].toBool());
        // Discover puts YouTube first
        const QVariantList groups = c.discover();
        QCOMPARE(groups.first().toMap()[u"id"_s].toString(), u"webapps"_s);
        QCOMPARE(ids(groups.first().toMap()[u"items"_s].toList()).first(), u"youtube"_s);
    }

    void search_ranks_names_over_descriptions_and_needs_every_word()
    {
        Catalog c;
        c.setBuiltinWebapps(builtinWebapps());
        c.setServer(QJsonArray{server(u"gitea"_s, u"service"_s, {{u"description"_s, u"Git service like github for yourself"_s}}),
                               server(u"qwen"_s, u"model"_s, {{u"name"_s, u"Qwen3 8B"_s}})});
        QCOMPARE(ids(c.search(u"youtube"_s)).first(), u"youtube"_s);
        QVERIFY(ids(c.search(u"youtube"_s)).contains(u"youtube-music"_s));
        // "github": the web app named GitHub before the service that only mentions it
        QCOMPARE(ids(c.search(u"github"_s)).first(), u"github"_s);
        QVERIFY(ids(c.search(u"github"_s)).contains(u"gitea"_s));
        QCOMPARE(ids(c.search(u"qwen 8b"_s)), (QStringList{u"qwen"_s}));
        QVERIFY(c.search(u"qwen nonsense"_s).isEmpty());
        QVERIFY(c.search(u"zzz"_s).isEmpty());
        QCOMPARE(c.search(u"  YOUTUBE  "_s).size(), c.search(u"youtube"_s).size());   // trimmed, any case
        // a search inside one section
        QCOMPARE(ids(c.section(u"models"_s, u"qwen"_s)), (QStringList{u"qwen"_s}));
        QVERIFY(c.section(u"models"_s, u"youtube"_s).isEmpty());
    }

    // ---- the engine against the controller ---------------------------------------------------------------
    void refresh_loads_catalogue_installed_models_and_hardware()
    {
        start(true);
        loaded();
        // the installed list, the models, the model variants and the hardware profile come from their own
        // requests that answer after the catalogue: wait for everything that is asserted
        QVERIFY(waitFor([this] {
            return m_e->app(u"uptime-kuma"_s)[u"runtimePort"_s].toInt() == 3001 && m_e->profileId() == u"cpu-only"_s
                && ids(m_e->list(u"models"_s)).size() == 3
                && m_e->app(u"qwen3-8b"_s)[u"modelVariants"_s].toList().size() == 2;
        }));
        QVERIFY(m_e->isAdmin());
        QVERIFY(ids(m_e->list(u"apps"_s)).contains(u"searxng"_s));
        QVERIFY(ids(m_e->list(u"apps"_s)).contains(u"openclaw"_s));       // agent frameworks live in Apps
        QVERIFY(ids(m_e->list(u"plugins"_s)).contains(u"web-search"_s));
        QVERIFY(ids(m_e->list(u"plugins"_s)).contains(u"mcp-memory"_s));
        QCOMPARE(ids(m_e->list(u"models"_s)), (QStringList{u"flux-dev-gguf"_s, u"gemma-3-1b"_s, u"qwen3-8b"_s}));
        QCOMPARE(m_e->app(u"gemma-3-1b"_s)[u"fit"_s].toString(), u"green"_s);
        QCOMPARE(m_e->app(u"qwen3-8b"_s)[u"fit"_s].toString(), u"yellow"_s);
        QCOMPARE(m_e->app(u"flux-dev-gguf"_s)[u"fitText"_s].toString(), u"Does not fit this device"_s);
        QCOMPARE(m_e->profileId(), u"cpu-only"_s);
        QVERIFY(m_e->app(u"uptime-kuma"_s)[u"installed"_s].toBool());
        QVERIFY(m_e->app(u"uptime-kuma"_s)[u"updateAvailable"_s].toBool());
        QCOMPARE(m_e->app(u"uptime-kuma"_s)[u"runtimePort"_s].toInt(), 3001);
        QCOMPARE(m_e->app(u"uptime-kuma"_s)[u"installedVersion"_s].toString(), u"1.0.0"_s);
        QCOMPARE(ids(m_e->list(u"updates"_s)), (QStringList{u"uptime-kuma"_s}));
        QVERIFY(ids(m_e->list(u"installed"_s)).contains(u"smolagents"_s));
        // models: the variants carry the controller's fit
        const QVariantList vs = m_e->app(u"qwen3-8b"_s)[u"modelVariants"_s].toList();
        QCOMPARE(vs.size(), 2);
        QCOMPARE(vs[1].toMap()[u"fit"_s].toString(), u"red"_s);
        // the detail route adds what the list lacks
        m_e->loadDetail(u"searxng"_s);
        QVERIFY(waitFor([this] { return m_e->app(u"searxng"_s)[u"detailLoaded"_s].toBool(); }));
        QCOMPARE(m_e->app(u"searxng"_s)[u"homepage"_s].toString(), u"https://example.org/searxng"_s);
        bool sawDocker = false;
        for (const QVariant &r : m_e->asks(u"searxng"_s))
            if (r.toMap()[u"value"_s].toString() == u"Needs Docker"_s)
                sawDocker = true;
        QVERIFY(sawDocker);
    }

    void a_refused_catalogue_still_leaves_web_apps_and_themes()
    {
        start(false, 403);
        QVERIFY(waitFor([this] { return m_e->errorKind() == u"forbidden"_s; }));
        QVERIFY(!m_e->loaded());
        QVERIFY(!m_e->errorText().isEmpty());
        QVERIFY(ids(m_e->list(u"webapps"_s)).contains(u"youtube"_s));
        QVERIFY(m_e->count(u"themes"_s) >= 3);
        QVERIFY(m_e->list(u"apps"_s).isEmpty());
    }

    // ---- install state machine ---------------------------------------------------------------------------
    void an_administrator_who_is_refused_gets_the_controllers_own_sentence()
    {
        start(true);
        loaded();
        m_fake->config({{u"outcome"_s, QJsonObject{{u"gitea"_s, QJsonObject{{u"status"_s, 403}, {u"body"_s, QJsonObject{{u"detail"_s, u"manifest signature verification failed"_s}}}}}}}});
        m_e->install(u"gitea"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gitea"_s) == u"failed"_s; }));
        const QString err = m_e->installState(u"gitea"_s)[u"error"_s].toString();
        QCOMPARE(err, u"manifest signature verification failed"_s);
        QVERIFY(!err.contains(u"administrator"_s));
    }

    void a_post_that_outlives_the_client_timeout_is_settled_by_the_progress_route()
    {
        start(true);
        loaded();
        // the harness client gives up on a request after 8 s; a real model download or image pull takes longer
        m_fake->config({{u"install_delay_ms"_s, 9800}});
        m_e->install(u"gemma-3-1b"_s);
        QVERIFY(waitFor([&] { return m_e->installState(u"gemma-3-1b"_s).value(u"error"_s).toString() == u"lost"_s; }, 14000));
        QCOMPARE(stateOf(u"gemma-3-1b"_s), u"installing"_s);                         // still working, not failed
        QCOMPARE(m_e->installState(u"gemma-3-1b"_s).value(u"text"_s).toString(), u"Waiting for the controller"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gemma-3-1b"_s) == u"installed"_s; }, 12000));
        QVERIFY(m_e->app(u"gemma-3-1b"_s)[u"installed"_s].toBool());
    }

    void a_detail_asked_for_before_the_catalogue_arrives_is_fetched_when_it_does()
    {
        start(true);
        m_e->loadDetail(u"searxng"_s);   // as `taos-store --app searxng` does at startup
        QVERIFY(waitFor([this] { return m_e->app(u"searxng"_s)[u"detailLoaded"_s].toBool(); }));
        QCOMPARE(m_e->app(u"searxng"_s)[u"homepage"_s].toString(), u"https://example.org/searxng"_s);
    }

    void a_member_is_told_that_installing_needs_an_administrator()
    {
        start(false);
        loaded();
        QSignalSpy done(m_e, &StoreEngine::installFinished);
        m_e->install(u"gitea"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QCOMPARE(done[0][1].toBool(), false);
        const QVariantMap st = m_e->installState(u"gitea"_s);
        QCOMPARE(st[u"state"_s].toString(), u"failed"_s);
        QVERIFY2(st[u"error"_s].toString().contains(u"administrator"_s), qPrintable(st[u"error"_s].toString()));
        QVERIFY(st[u"error"_s].toString().contains(u"Admin access required"_s));      // and the controller's own words
        QVERIFY(!m_e->app(u"gitea"_s)[u"installed"_s].toBool());
        m_e->dismiss(u"gitea"_s);
        QCOMPARE(stateOf(u"gitea"_s), u"idle"_s);
        // removing is refused the same way
        m_e->remove(u"uptime-kuma"_s);
        QVERIFY(waitFor([&] { return stateOf(u"uptime-kuma"_s) == u"failed"_s; }));
        QVERIFY(m_e->app(u"uptime-kuma"_s)[u"installed"_s].toBool());
    }

    void install_goes_through_progress_to_installed_and_remove_undoes_it()
    {
        start(true);
        loaded();
        m_fake->config({{u"install_delay_ms"_s, 2200}});
        m_e->install(u"gitea"_s);
        QCOMPARE(stateOf(u"gitea"_s), u"installing"_s);
        // the progress route is polled while it works
        QVERIFY(waitFor([&] { return m_e->installState(u"gitea"_s).value(u"percent"_s).toDouble() >= 40; }, 6000));
        QCOMPARE(m_e->installState(u"gitea"_s).value(u"text"_s).toString(), u"Downloading"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gitea"_s) == u"installed"_s; }, 8000));
        QVERIFY(m_e->app(u"gitea"_s)[u"installed"_s].toBool());
        QVERIFY(ids(m_e->list(u"installed"_s)).contains(u"gitea"_s));
        const QJsonArray calls = m_fake->callsTo(u"/api/store/install-v2"_s);
        QCOMPARE(calls.size(), 1);
        const QJsonObject body = calls[0].toObject()[u"body"_s].toObject();
        QCOMPARE(body[u"app_id"_s].toString(), u"gitea"_s);
        QCOMPARE(body[u"target_remote"_s].toString(), u"local"_s);
        QVERIFY(!body.contains(u"accepted"_s));
        // remove
        m_e->dismiss(u"gitea"_s);
        m_e->remove(u"gitea"_s);
        QCOMPARE(stateOf(u"gitea"_s), u"removing"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gitea"_s) == u"idle"_s; }));
        QVERIFY(!m_e->app(u"gitea"_s)[u"installed"_s].toBool());
        QCOMPARE(m_fake->callsTo(u"/api/store/uninstall"_s).size(), 1);
        // removing what is not installed: the controller's 404 in words
        m_e->remove(u"gitea"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gitea"_s) == u"failed"_s; }));
        QVERIFY(m_e->installState(u"gitea"_s)[u"error"_s].toString().contains(u"not installed"_s));
    }

    void a_docker_app_asks_before_anything_is_pulled()
    {
        start(true);
        loaded();
        m_e->install(u"searxng"_s);
        const QVariantMap st = m_e->installState(u"searxng"_s);
        QCOMPARE(st[u"state"_s].toString(), u"consent"_s);
        QCOMPARE(st[u"consent"_s].toString(), u"docker"_s);
        QVERIFY(st[u"consentText"_s].toString().contains(u"Docker"_s));
        QCOMPARE(m_fake->callsTo(u"/api/store/install-v2"_s).size(), 0);          // nothing sent yet
        m_e->cancelConsent(u"searxng"_s);
        QCOMPARE(stateOf(u"searxng"_s), u"idle"_s);
        QCOMPARE(m_fake->callsTo(u"/api/store/install-v2"_s).size(), 0);
        m_e->install(u"searxng"_s);
        m_e->acceptConsent(u"searxng"_s);
        QVERIFY(waitFor([&] { return stateOf(u"searxng"_s) == u"installed"_s; }));
        QCOMPARE(m_fake->callsTo(u"/api/store/install-v2"_s).size(), 1);
        // a manifest that says runtime: either does not nag
        m_e->install(u"n8n"_s);
        QVERIFY(waitFor([&] { return stateOf(u"n8n"_s) == u"installed"_s; }));
    }

    void non_commercial_weights_ask_for_the_licence_the_controller_names()
    {
        start(true);
        loaded();
        m_e->install(u"musicgen"_s);
        QVERIFY(waitFor([&] { return stateOf(u"musicgen"_s) == u"consent"_s; }));
        QCOMPARE(m_e->installState(u"musicgen"_s)[u"consent"_s].toString(), u"license"_s);
        QVERIFY(m_e->installState(u"musicgen"_s)[u"consentText"_s].toString().contains(u"CC-BY-NC 4.0"_s));
        QVERIFY(m_e->installState(u"musicgen"_s)[u"consentText"_s].toString().contains(u"non-commercial use only"_s));
        m_e->cancelConsent(u"musicgen"_s);
        QVERIFY(!m_e->app(u"musicgen"_s)[u"installed"_s].toBool());
        m_e->install(u"musicgen"_s);
        QVERIFY(waitFor([&] { return stateOf(u"musicgen"_s) == u"consent"_s; }));
        m_e->acceptConsent(u"musicgen"_s);
        QVERIFY(waitFor([&] { return stateOf(u"musicgen"_s) == u"installed"_s; }));
        const QJsonArray calls = m_fake->callsTo(u"/api/store/install-v2"_s);
        QVERIFY(!calls[calls.size() - 2].toObject()[u"body"_s].toObject().contains(u"accepted"_s));
        QCOMPARE(calls[calls.size() - 1].toObject()[u"body"_s].toObject()[u"accepted"_s].toBool(), true);
    }

    void server_errors_are_shown_in_the_controllers_words()
    {
        start(true);
        loaded();
        m_fake->config({{u"outcome"_s, QJsonObject{{u"gitea"_s, QJsonObject{{u"status"_s, 500}, {u"body"_s, QJsonObject{{u"error"_s, u"installer crashed"_s}}}}},
                                                      {u"openclaw"_s, QJsonObject{{u"status"_s, 409}, {u"body"_s, QJsonObject{{u"error"_s, u"already installed"_s}}}}}}}});
        m_e->install(u"gitea"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gitea"_s) == u"failed"_s; }));
        QCOMPARE(m_e->installState(u"gitea"_s)[u"error"_s].toString(), u"installer crashed"_s);
        m_e->install(u"openclaw"_s);
        QVERIFY(waitFor([&] { return stateOf(u"openclaw"_s) == u"failed"_s; }));
        QCOMPARE(m_e->installState(u"openclaw"_s)[u"error"_s].toString(), u"already installed"_s);
    }

    void a_model_variant_is_sent_with_the_install()
    {
        start(true);
        loaded();
        m_e->install(u"qwen3-8b"_s, u"q4"_s);
        QVERIFY(waitFor([&] { return stateOf(u"qwen3-8b"_s) == u"installed"_s; }));
        QCOMPARE(m_fake->callsTo(u"/api/store/install-v2"_s).last().toObject()[u"body"_s].toObject()[u"variant_id"_s].toString(), u"q4"_s);
        QVERIFY(m_e->app(u"qwen3-8b"_s)[u"installed"_s].toBool());
    }

    // ---- web apps, end to end through the engine ----------------------------------------------------------
    void a_web_app_is_added_launched_cloned_cleared_and_removed_through_the_engine()
    {
        start(true);
        loaded();
        WebAppsService *w = m_e->web();
        QSignalSpy done(w, &WebAppsService::finished);
        m_e->installWebapp(u"youtube"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY2(done[0][1].toBool(), qPrintable(done[0][3].toString()));
        const QString id = done[0][2].toString();
        QVERIFY(waitFor([&] { return w->instances().size() == 1; }));
        QVERIFY(m_e->app(u"youtube"_s)[u"installed"_s].toBool());
        QCOMPARE(m_e->app(u"youtube"_s)[u"instances"_s].toInt(), 1);
        QCOMPARE(w->instance(id)[u"name"_s].toString(), u"YouTube"_s);
        QCOMPARE(w->instance(id)[u"source"_s].toString(), u"youtube"_s);
        QVERIFY(QFileInfo(m_data + u"/applications/taos-webapp-"_s + id + u".desktop"_s).isFile());
        QVERIFY(ids(m_e->installedRows()).contains(id));
        QVERIFY(w->browserAvailable());
        // adding it again names the second one
        done.clear();
        m_e->installWebapp(u"youtube"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(waitFor([&] { return w->instances().size() == 2; }));
        QStringList names;
        for (const QVariant &v : w->instances())
            names << v.toMap()[u"name"_s].toString();
        names.sort();
        QCOMPARE(names, (QStringList{u"YouTube"_s, u"YouTube (2)"_s}));
        // launch: the fake browser takes the window class
        done.clear();
        w->launch(id);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(done[0][1].toBool());
        QVERIFY(waitFor([&] { return w->instance(id)[u"running"_s].toBool(); }, 10000));
        // clone and clear are refused while it is open, in words
        done.clear();
        w->clone(id);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(!done[0][1].toBool());
        QVERIFY(done[0][3].toString().contains(u"Close its window"_s));
        writeFile(m_stop, "stop");
        QVERIFY(waitFor([&] { w->refresh(); QTest::qWait(100); return !w->instance(id)[u"running"_s].toBool(); }, 10000));
        QFile::remove(m_stop);
        // clone keeps the sign-in
        writeFile(w->options().root + u'/' + id + u"/profile/Default/Cookies"_s, "session");
        done.clear();
        w->clone(id, u"YouTube for kids"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY2(done[0][1].toBool(), qPrintable(done[0][3].toString()));
        const QString cid = done[0][2].toString();
        QVERIFY(cid != id);
        QFile ck(w->options().root + u'/' + cid + u"/profile/Default/Cookies"_s);
        QVERIFY(ck.open(QIODevice::ReadOnly));
        QCOMPARE(ck.readAll(), QByteArray("session"));
        QVERIFY(waitFor([&] { return w->instances().size() == 3; }));
        // rename, clear data, remove
        done.clear();
        w->rename(cid, u"Kids"_s);
        QVERIFY(waitFor([&] { return done.count() == 1 && w->instance(cid)[u"name"_s].toString() == u"Kids"_s; }));
        {
            QFile dt(m_data + u"/applications/taos-webapp-"_s + cid + u".desktop"_s);
            QVERIFY(dt.open(QIODevice::ReadOnly));
            QVERIFY(QString::fromUtf8(dt.readAll()).contains(u"Name=Kids\n"_s));
        }
        done.clear();
        w->clearData(cid);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(done[0][1].toBool());
        QVERIFY(!QFileInfo::exists(w->options().root + u'/' + cid + u"/profile/Default/Cookies"_s));
        done.clear();
        w->remove(cid);
        QVERIFY(waitFor([&] { return done.count() == 1 && w->instances().size() == 2; }));
        QVERIFY(!QFileInfo::exists(m_data + u"/applications/taos-webapp-"_s + cid + u".desktop"_s));
    }

    void add_any_website_checks_the_address_first()
    {
        start(true);
        loaded();
        WebAppsService *w = m_e->web();
        QVERIFY(!w->checkUrl(u"example.com"_s).isEmpty() == false);
        QVERIFY(!w->checkUrl(u"javascript:alert(1)"_s).isEmpty());
        QVERIFY(!w->checkUrl(u"http://example.com"_s).isEmpty());
        QVERIFY(w->checkUrl(u"http://example.com"_s, true).isEmpty());
        QVERIFY(!w->checkUrl(u"file:///etc/passwd"_s).isEmpty());
        QSignalSpy done(w, &WebAppsService::finished);
        m_e->addWebsite(u"javascript:alert(1)"_s, {});
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(!done[0][1].toBool());
        QCOMPARE(done[0][4].toString(), u"invalid_url"_s);
        QVERIFY(w->instances().isEmpty());
        done.clear();
        m_e->addWebsite(u"https://news.example.org/feed"_s, u"News"_s, false);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(done[0][1].toBool());
        QVERIFY(waitFor([&] { return w->instances().size() == 1; }));
        QCOMPARE(w->instances()[0].toMap()[u"source"_s].toString(), u"custom"_s);
    }

    void without_a_browser_the_banner_text_says_what_to_install()
    {
        qputenv("TAOS_WEBAPP_BROWSER", "/nonexistent/chromium");
        start(true);
        QVERIFY(waitFor([&] { return !m_e->web()->browserAvailable() && !m_e->web()->browserText().isEmpty(); }));
        QVERIFY(m_e->web()->browserText().contains(u"No browser engine installed"_s));
        QVERIFY(m_e->web()->browserText().contains(u"chromium"_s));
        qputenv("TAOS_WEBAPP_BROWSER", QByteArray(FAKE_BROWSER));
    }

    // ---- themes ---------------------------------------------------------------------------------------------
    void themes_are_listed_and_applied_through_the_theme_engine()
    {
        start(true);
        writeFile(m_tmp.path() + u"/themes/my-theme/theme.json"_s,
                  "{\"id\":\"my-theme\",\"name\":\"My Theme\",\"variants\":[\"dark\"],\"colors\":{\"dark\":{}}}");
        m_e->setThemePaths(themePaths());
        ThemesService *t = m_e->themesService();
        QStringList got;
        for (const QVariant &v : t->themes())
            got << v.toMap()[u"id"_s].toString();
        QVERIFY(got.contains(u"graphite"_s));
        QVERIFY(got.contains(u"graphite-ice"_s));
        QVERIFY(got.contains(u"graphite-warm"_s));
        QCOMPARE(t->originOf(u"graphite"_s), u"Built in"_s);
        QCOMPARE(t->originOf(u"my-theme"_s), u"Yours"_s);
        QCOMPARE(t->apply(u"graphite-ice"_s), true);
        QFile f(themePaths().appearancePath);
        QVERIFY(f.open(QIODevice::ReadOnly));
        QCOMPARE(QJsonDocument::fromJson(f.readAll()).object()[u"theme"_s].toString(), u"graphite-ice"_s);
        bool current = false;
        for (const QVariant &v : t->themes())
            if (v.toMap()[u"id"_s].toString() == u"graphite-ice"_s)
                current = v.toMap()[u"current"_s].toBool();
        QVERIFY(current);
        QCOMPARE(t->apply(u"no-such-theme"_s), false);
    }

    void installed_lists_every_kind()
    {
        start(true);
        loaded();
        WebAppsService *w = m_e->web();
        QSignalSpy done(w, &WebAppsService::finished);
        m_e->installWebapp(u"github"_s);
        QVERIFY(waitFor([&] { return done.count() == 1 && w->instances().size() == 1; }));
        QSet<QString> kinds;
        for (const QVariant &v : m_e->installedRows())
            kinds << v.toMap()[u"kind"_s].toString();
        QVERIFY(kinds.contains(u"app"_s));
        QVERIFY(kinds.contains(u"agent"_s));
        QVERIFY(kinds.contains(u"webapp"_s));
        QVERIFY(kinds.contains(u"theme"_s));
        m_e->install(u"gemma-3-1b"_s);
        QVERIFY(waitFor([&] { return stateOf(u"gemma-3-1b"_s) == u"installed"_s; }));
        QVERIFY(waitFor([&] { for (const QVariant &v : m_e->installedRows()) if (v.toMap()[u"kind"_s].toString() == u"model"_s) return true; return false; }));
    }

    // ---- Settings > Apps ---------------------------------------------------------------------------------
    void the_apps_overview_lists_native_container_and_web_apps()
    {
        start(true);
        const QString dir = m_tmp.path() + u"/desktop-files"_s;
        writeFile(dir + u"/my.taos.agents.desktop"_s, "[Desktop Entry]\nType=Application\nName=Agents\nComment=The agents\nExec=taos-agents %U\nIcon=my.taos.agents\nX-taOS-AppId=my.taos.agents\n");
        writeFile(dir + u"/my.taos.store.desktop"_s, "[Desktop Entry]\nType=Application\nName=Store\nExec=taos-store\nX-taOS-AppId=my.taos.store\n");
        writeFile(dir + u"/taos-webapp-abcdefghij.desktop"_s, "[Desktop Entry]\nName=Web\nExec=taos-webapp launch abcdefghij\nX-taOS-AppId=taos-webapp-abcdefghij\n");
        writeFile(dir + u"/taos-webapp-k3j9x0a1bc.desktop"_s, "[Desktop Entry]\nName=YouTube\nExec=taos-webapp launch k3j9x0a1bc\nStartupWMClass=chrome-www.youtube.com__-k3j9x0a1bc\nX-taOS-AppId=chrome-www.youtube.com__-k3j9x0a1bc\nX-taOS-WebApp=k3j9x0a1bc\n");
        writeFile(dir + u"/firefox.desktop"_s, "[Desktop Entry]\nName=Firefox\nExec=firefox\n");
        writeFile(dir + u"/hidden.desktop"_s, "[Desktop Entry]\nName=Hidden\nExec=x\nNoDisplay=true\nX-taOS-AppId=my.taos.hidden\n");
        AppsOverview o;
        o.setWebappOptions(webOpts());
        o.setDesktopDirs({dir});
        o.setClientForTest(m_host->session().client());
        QList<QPair<QString, QStringList>> launched;
        o.setLauncher([&](const QString &p, const QStringList &a) { launched << qMakePair(p, a); return true; });
        o.refresh();
        QVERIFY(waitFor([&] { return o.containersLoaded(); }));
        QStringList nat;
        for (const QVariant &v : o.native())
            nat << v.toMap()[u"name"_s].toString();
        QCOMPARE(nat, (QStringList{u"Agents"_s, u"Store"_s}));           // taOS apps only: no web app entry, no stranger, nothing hidden
        QStringList cont;
        for (const QVariant &v : o.containers())
            cont << v.toMap()[u"name"_s].toString();
        QVERIFY(cont.contains(u"Uptime Kuma"_s));
        QVERIFY(cont.contains(u"SmolAgents"_s));
        QVERIFY(o.containers().first().toMap()[u"subtitle"_s].toString().size() > 0);
        QVERIFY(o.openNative(u"my.taos.agents"_s));
        QCOMPARE(launched.last().first, u"taos-agents"_s);
        QVERIFY(launched.last().second.isEmpty());                        // %U is not an argument
        QVERIFY(!o.openNative(u"my.taos.nothing"_s));
        QVERIFY(o.openInStore(u"uptime-kuma"_s));
        QCOMPARE(launched.last().second, (QStringList{u"--app"_s, u"uptime-kuma"_s}));
        // web apps through the same engine
        QSignalSpy done(o.web(), &WebAppsService::finished);
        o.web()->add(u"https://github.com"_s, u"GitHub"_s, u"github"_s, false);
        QVERIFY(waitFor([&] { return done.count() == 1 && o.webInstances().size() == 1; }));
        const QVariantMap row = o.webInstances().first().toMap();
        QVERIFY(row.contains(u"sizeText"_s));
        QCOMPARE(row[u"lastUsedText"_s].toString(), u"Never opened"_s);
        QCOMPARE(row[u"host"_s].toString(), u"github.com"_s);
    }

    // ---- the saved catalogue, artwork and the Discover page ------------------------------------------------
    void icons_resolve_bundled_then_family_then_controller_then_nothing()
    {
        Catalog c;
        c.setArt(artIndex(), u"file:///assets"_s);
        QCOMPARE(c.iconUrl(u"gitea"_s, u"https://example.org/x.png"_s), u"file:///assets/icons/gitea.png"_s);   // bundled wins
        QCOMPARE(c.iconUrl(u"youtube"_s), u"file:///assets/icons/webapp-youtube.svg"_s);                        // svg by index
        QCOMPARE(c.iconUrl(u"qwen3-8b"_s), u"file:///assets/icons/qwen.png"_s);                                  // family prefix
        QCOMPARE(c.iconUrl(u"mystery"_s, u"https://example.org/x.png"_s), u"https://example.org/x.png"_s);      // controller icon
        QCOMPARE(c.iconUrl(u"mystery"_s, u"mdi:robot"_s), QString());                                           // not an address
        QCOMPARE(c.iconUrl(u"mystery"_s), QString());                                                           // styled tile
        // an icon listed for an id but missing from the index is not used
        QCOMPARE(Catalog().iconUrl(u"gitea"_s), QString());
        QCOMPARE(c.coverUrl(u"gitea"_s), u"file:///assets/covers/gitea.jpg"_s);
        QCOMPARE(c.coverUrl(u"sd-webui"_s), u"file:///assets/covers/stable-diffusion.jpg"_s);                  // alias
        QCOMPARE(c.coverUrl(u"nothing"_s), QString());
    }

    void snapshot_shows_only_until_the_controller_answers_and_fills_gaps()
    {
        Catalog c;
        c.setArt(artIndex(), u"file:///assets"_s);
        c.setBuiltinWebapps(builtinWebapps());
        c.setSnapshot(QJsonArray{server(u"gitea"_s, u"service"_s, {{u"stars"_s, 45000}, {u"tagline"_s, u"Git, hosted by you"_s}}),
                                 server(u"qwen3-8b"_s, u"model"_s)});
        QVERIFY(c.usingSnapshot());
        QCOMPARE(c.entry(u"gitea"_s)[u"source"_s].toString(), u"snapshot"_s);
        QCOMPARE(c.entry(u"gitea"_s)[u"starsText"_s].toString(), u"45.0k"_s);
        QCOMPARE(c.entry(u"gitea"_s)[u"iconUrl"_s].toString(), u"file:///assets/icons/gitea.png"_s);
        QCOMPARE(c.entry(u"gitea"_s)[u"coverUrl"_s].toString(), u"file:///assets/covers/gitea.jpg"_s);
        QVERIFY(ids(c.section(u"models"_s)).contains(u"qwen3-8b"_s));
        // the controller's catalogue replaces it; stars and tagline it lacks come from the saved entry
        c.setServer(QJsonArray{server(u"gitea"_s, u"service"_s)});
        QVERIFY(!c.usingSnapshot());
        QCOMPARE(c.entry(u"gitea"_s)[u"source"_s].toString(), u"controller"_s);
        QCOMPARE(c.entry(u"gitea"_s)[u"stars"_s].toInt(), 45000);
        QCOMPARE(c.entry(u"gitea"_s)[u"tagline"_s].toString(), u"Git, hosted by you"_s);
        QVERIFY(!c.has(u"qwen3-8b"_s));
        QCOMPARE(starsText(950), u"950"_s);
        QCOMPARE(starsText(0), QString());
        QCOMPARE(starsText(379000), u"379.0k"_s);
    }

    void discover_page_has_a_hero_and_shelves_and_leaves_empty_ones_out()
    {
        Catalog c;
        c.setArt(artIndex(), u"file:///assets"_s);
        c.setCurated(QJsonDocument::fromJson(R"({"featured": "gitea", "shelves": {"popular": ["gitea", "searxng", "n8n"],
                                                  "subscriptions": ["searxng", "n8n", "gitea"]}})").object());
        c.setBuiltinWebapps(builtinWebapps());
        c.setServer(QJsonArray{server(u"gitea"_s, u"service"_s), server(u"searxng"_s, u"service"_s), server(u"n8n"_s, u"service"_s)});
        const QVariantMap page = c.discoverPage();
        QCOMPARE(page[u"hero"_s].toMap()[u"id"_s].toString(), u"gitea"_s);
        QStringList shelfIds;
        for (const QVariant &v : page[u"shelves"_s].toList())
            shelfIds << v.toMap()[u"id"_s].toString();
        QVERIFY(shelfIds.contains(u"popular"_s));
        QVERIFY(shelfIds.contains(u"subscriptions"_s));
        QVERIFY(shelfIds.contains(u"webapps"_s));
        QVERIFY(!shelfIds.contains(u"models"_s));          // nothing to show: no shelf
        QVERIFY(!shelfIds.contains(u"agents"_s));
        for (const QVariant &v : page[u"shelves"_s].toList())
            if (v.toMap()[u"id"_s].toString() == u"popular"_s)
                QVERIFY(!ids(v.toMap()[u"items"_s].toList()).contains(u"gitea"_s));   // the hero is not shown twice
        // no cover anywhere: no hero rather than a blank one
        Catalog d;
        d.setServer(QJsonArray{server(u"gitea"_s, u"service"_s)});
        QVERIFY(d.discoverPage()[u"hero"_s].toMap().isEmpty());
    }

    void agents_and_mcp_are_lenses_over_the_kinds()
    {
        Catalog c;
        c.setServer(QJsonArray{server(u"openclaw"_s, u"agent-framework"_s), server(u"mcp-memory"_s, u"mcp"_s), server(u"gitea"_s, u"service"_s)});
        QCOMPARE(ids(c.section(u"agents"_s)), (QStringList{u"openclaw"_s}));
        QCOMPARE(ids(c.section(u"mcp"_s)), (QStringList{u"mcp-memory"_s}));
        QCOMPARE(c.count(u"agents"_s), 1);
        QCOMPARE(c.chips(u"agents"_s).first().toMap()[u"count"_s].toInt(), 1);
    }

    void the_saved_catalogue_loads_from_a_folder_and_the_sidebar_hides_empty_sections()
    {
        QTemporaryDir dir;
        writeFile(dir.filePath(u"catalog/art-index.json"_s), QJsonDocument(artIndex()).toJson());
        writeFile(dir.filePath(u"catalog/snapshot.json"_s),
                  QJsonDocument(QJsonObject{{u"entries"_s, QJsonArray{server(u"gitea"_s, u"service"_s)}}}).toJson());
        qputenv("TAOS_STORE_CATALOG_DIR", dir.filePath(u"catalog"_s).toUtf8());
        StoreEngine e;      // not running: it never asks the controller
        e.setWebappOptions(webOpts());
        e.setThemePaths(themePaths());
        e.setBuiltinWebapps(builtinWebapps());
        e.loadBundledCatalog();
        qunsetenv("TAOS_STORE_CATALOG_DIR");
        QVERIFY(e.usingSnapshot());
        QVERIFY(e.statusText().contains(u"saved with this app"_s));
        QVERIFY(e.catalog().entry(u"gitea"_s)[u"iconUrl"_s].toString().startsWith(u"file:"_s));
        QStringList sec;
        for (const QVariant &v : e.sections())
            sec << v.toMap()[u"id"_s].toString();
        QVERIFY(sec.contains(u"apps"_s));
        QVERIFY(sec.contains(u"discover"_s));
        QVERIFY(sec.contains(u"installed"_s));
        QVERIFY(!sec.contains(u"models"_s));      // the engine has none: not shown empty
        QVERIFY(!sec.contains(u"agents"_s));
        QVERIFY(!sec.contains(u"mcp"_s));
    }

    void relative_days_and_sizes_read_as_words()
    {
        const QDateTime now = QDateTime(QDate(2026, 11, 22), QTime(12, 0), Qt::UTC);
        QCOMPARE(relativeDay(QDateTime(), now), u"Never opened"_s);
        QCOMPARE(relativeDay(now.addSecs(-3600), now), u"Today"_s);
        QCOMPARE(relativeDay(now.addDays(-1), now), u"Yesterday"_s);
        QCOMPARE(relativeDay(now.addDays(-4), now), u"4 days ago"_s);
        QCOMPARE(bytesText(0), u"0 KB"_s);
        QCOMPARE(bytesText(2048), u"2 KB"_s);
        QCOMPARE(bytesText(5 * 1024 * 1024), u"5.0 MB"_s);
        QCOMPARE(bytesText(3LL * 1024 * 1024 * 1024), u"3.0 GB"_s);
    }
};

QTEST_MAIN(TstStoreLogic)
#include "tst_store_logic.moc"
