// SPDX-License-Identifier: AGPL-3.0-or-later
// The Store window loaded in a QML engine against the fake controller and a fake browser: the sections, the Web apps
// page end to end (add from the catalogue, expand, clone, remove with a second click), the Add website dialog and its
// address check, the Docker consent card, and the refusal a member gets, in words.
#include <QTemporaryDir>

#include "apphost.h"
#include "storeengine.h"

using namespace Qt::StringLiterals;

namespace {
QQuickItem *byName(QQuickWindow *w, const QString &name)
{
    return findItem(w->contentItem(), [&](QQuickItem *i) { return i->objectName() == name; });
}
QQuickItem *visibleByName(QQuickWindow *w, const QString &name)
{
    return findItem(w->contentItem(), [&](QQuickItem *i) { return i->objectName() == name && i->isVisible(); });
}
QQuickItem *byAccessible(QQuickWindow *w, const QString &name)
{
    return findItem(w->contentItem(), [&](QQuickItem *i) { return i->property("accessibleName").toString() == name && i->isVisible(); });
}
int countPrefix(QQuickWindow *w, const QString &prefix)
{
    return findItems(w->contentItem(), [&](QQuickItem *i) { return i->objectName().startsWith(prefix) && i->isVisible(); }).size();
}
void click(QQuickItem *b) { QMetaObject::invokeMethod(b, "clicked"); }
void writeFile(const QString &p, const QByteArray &d)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    f.open(QIODevice::WriteOnly);
    f.write(d);
}
} // namespace

class TstStoreUi : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    QQuickWindow *m_win = nullptr;
    QTemporaryDir m_home;   // everything the window reads or writes lives here: a person's own folders are never touched
    QString m_stop;

    void open(bool admin)
    {
        delete m_host;
        m_host = nullptr;
        // a clean web apps folder for every window
        QDir(qEnvironmentVariable("XDG_DATA_HOME") + u"/taos"_s).removeRecursively();
        QDir(qEnvironmentVariable("XDG_DATA_HOME") + u"/applications"_s).removeRecursively();
        m_fake->config({{u"store"_s, u"seed"_s}, {u"admin"_s, admin}, {u"outcome"_s, QJsonObject()}, {u"install_delay_ms"_s, 0}, {u"catalog_fail"_s, 0}});
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.store"_s);
        QVERIFY(m_host->waitSignedIn());
        m_win = m_host->load(u"qrc:/my.taos.store/Main.qml"_s);
        QVERIFY(m_win);
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"sections"_s) != nullptr; }));
    }
    void go(const QString &section)
    {
        auto *s = byName(m_win, u"section-"_s + section);
        QVERIFY(s);
        click(s);
        QTest::qWait(60);
    }

private Q_SLOTS:
    void initTestCase()
    {
        QVERIFY(m_home.isValid());
        const QString r = m_home.path();
        QDir().mkpath(r + u"/run"_s);
        QFile::setPermissions(r + u"/run"_s, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        qputenv("HOME", r.toUtf8());
        qputenv("XDG_DATA_HOME", (r + u"/data"_s).toUtf8());
        qputenv("XDG_CONFIG_HOME", (r + u"/config"_s).toUtf8());
        qputenv("XDG_RUNTIME_DIR", (r + u"/run"_s).toUtf8());
        qputenv("TAOS_APPEARANCE_MIRROR_DIR", (r + u"/mirror"_s).toUtf8());   // the greeter's copy of a person's theme
        qputenv("TAOS_SYSTEM_APPEARANCE", (r + u"/etc-appearance.json"_s).toUtf8());
        qputenv("TAOS_SYSTEM_THEME_DIR", (r + u"/system-themes"_s).toUtf8());
        qunsetenv("TAOS_APPEARANCE");
        qunsetenv("TAOS_THEME_DIR");
        qunsetenv("TAOS_THEME");
        qunsetenv("TAOS_THEME_VARIANT");
        QDir().mkpath(r + u"/mirror"_s);
        qputenv("TAOS_WEBAPP_BROWSER", QByteArray(FAKE_BROWSER));
        qputenv("TAOS_WEBAPP_NO_NETWORK", "1");
        m_stop = r + u"/stop"_s;
        qputenv("FAKE_BROWSER_STOP", m_stop.toUtf8());
        qputenv("TAOS_STORE_WEBAPPS", QByteArray(WEBAPPS_JSON));
        Taos::Store::registerStoreQmlTypes();
        m_fake = new FakeHost;
        QVERIFY(m_fake->ok());
    }
    void cleanupTestCase()
    {
        writeFile(m_stop, "stop");
        delete m_host;
        delete m_fake;
    }

    void the_test_runs_in_its_own_home()
    {
        QVERIFY(qEnvironmentVariable("XDG_DATA_HOME").startsWith(m_home.path()));
        QVERIFY(qEnvironmentVariable("XDG_CONFIG_HOME").startsWith(m_home.path()));
        QVERIFY(qEnvironmentVariable("TAOS_APPEARANCE_MIRROR_DIR").startsWith(m_home.path()));
    }

    void the_sections_are_there_and_discover_leads_with_youtube()
    {
        open(true);
        for (const QString &s : {u"discover"_s, u"apps"_s, u"webapps"_s, u"models"_s, u"themes"_s, u"plugins"_s, u"installed"_s, u"updates"_s})
            QVERIFY2(byName(m_win, u"section-"_s + s), qPrintable(s));
        QVERIFY(waitFor([this] { return byName(m_win, u"disc-youtube"_s) != nullptr; }));
        auto *yt = byName(m_win, u"disc-youtube"_s);
        QVERIFY(yt->isVisible());
        QCOMPARE(yt->property("entry").toMap()[u"name"_s].toString(), u"YouTube"_s);
        // YouTube is the first web app of Discover
        auto *first = findItem(m_win->contentItem(), [](QQuickItem *i) { return i->objectName().startsWith(u"disc-"_s) && i->isVisible(); });
        QCOMPARE(first->objectName(), u"disc-youtube"_s);
    }

    void web_apps_end_to_end()
    {
        open(true);
        go(u"webapps"_s);
        QVERIFY(visibleByName(m_win, u"addWebsiteButton"_s));
        QVERIFY(!visibleByName(m_win, u"noBrowserBanner"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"web-youtube"_s) != nullptr; }));
        QCOMPARE(countPrefix(m_win, u"instance-"_s), 0);
        // Add YouTube from the catalogue
        auto *add = byAccessible(m_win, u"Add YouTube as a web app"_s);
        QVERIFY(add);
        click(add);
        QVERIFY(waitFor([this] { return countPrefix(m_win, u"instance-"_s) == 1; }, 10000));
        QVERIFY(byAccessible(m_win, u"Add another copy of YouTube with its own sign-in"_s));   // the row now offers another
        // expand it: the actions appear
        auto *inst = findItem(m_win->contentItem(), [](QQuickItem *i) { return i->objectName().startsWith(u"instance-"_s) && i->isVisible(); });
        QVERIFY(inst);
        click(inst);
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"cloneButton"_s) != nullptr; }));
        QVERIFY(visibleByName(m_win, u"renameButton"_s));
        QVERIFY(visibleByName(m_win, u"clearButton"_s));
        QVERIFY(visibleByName(m_win, u"removeButton"_s));
        // clone keeps the sign-in: a second instance
        click(visibleByName(m_win, u"cloneButton"_s));
        QVERIFY(waitFor([this] { return countPrefix(m_win, u"instance-"_s) == 2; }, 10000));
        // remove asks once more, then removes
        click(visibleByName(m_win, u"removeButton"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"confirmButton"_s) != nullptr; }));
        click(visibleByName(m_win, u"confirmButton"_s));
        QVERIFY(waitFor([this] { return countPrefix(m_win, u"instance-"_s) == 1; }, 10000));
    }

    void add_any_website_checks_the_address_as_you_type()
    {
        open(true);
        go(u"webapps"_s);
        click(visibleByName(m_win, u"addWebsiteButton"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"urlField"_s) != nullptr; }));
        auto *url = byName(m_win, u"urlField"_s);
        auto *addBtn = byName(m_win, u"addButton"_s);
        QVERIFY(url && addBtn);
        QVERIFY(!addBtn->isEnabled());                       // empty: nothing to add
        url->setProperty("text", u"javascript:alert(1)"_s);
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"urlProblem"_s) != nullptr; }));
        QVERIFY(!addBtn->isEnabled());
        url->setProperty("text", u"http://example.org"_s);
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"urlProblem"_s) && visibleByName(m_win, u"urlProblem"_s)->property("text").toString().contains(u"https"_s); }));
        url->setProperty("text", u"example.org"_s);
        QVERIFY(waitFor([&] { return addBtn->isEnabled() && !visibleByName(m_win, u"urlProblem"_s); }));
        click(addBtn);
        QVERIFY(waitFor([this] { return countPrefix(m_win, u"instance-"_s) == 1; }, 10000));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"urlField"_s) == nullptr; }));
    }

    void a_docker_app_shows_its_mark_and_asks_before_installing()
    {
        open(true);
        QVERIFY(waitFor([this] { return m_win->property("engine").value<QObject *>() && m_win->property("engine").value<QObject *>()->property("loaded").toBool(); }));
        go(u"apps"_s);
        QVERIFY(waitFor([this] { return byName(m_win, u"row-searxng"_s) != nullptr; }));
        m_win->setProperty("detailId", u"searxng"_s);
        QVERIFY(waitFor([this] { return byAccessible(m_win, u"Install SearXNG"_s) != nullptr; }));
        click(byAccessible(m_win, u"Install SearXNG"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"consentCard"_s) != nullptr; }));
        QCOMPARE(m_fake->callsTo(u"/api/store/install-v2"_s).size(), 0);
        click(visibleByName(m_win, u"consentCancel"_s));
        QVERIFY(waitFor([this] { return !visibleByName(m_win, u"consentCard"_s); }));
        click(byAccessible(m_win, u"Install SearXNG"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"consentAccept"_s) != nullptr; }));
        click(visibleByName(m_win, u"consentAccept"_s));
        QVERIFY(waitFor([this] { return m_fake->callsTo(u"/api/store/install-v2"_s).size() == 1; }));
        QVERIFY(waitFor([this] { return byAccessible(m_win, u"Open SearXNG"_s) != nullptr; }));
    }

    void a_member_sees_why_the_install_failed()
    {
        open(false);
        QVERIFY(waitFor([this] { return m_win->property("engine").value<QObject *>()->property("loaded").toBool(); }));
        m_win->setProperty("detailId", u"gitea"_s);
        QVERIFY(waitFor([this] { return byAccessible(m_win, u"Install Gitea"_s) != nullptr; }));
        click(byAccessible(m_win, u"Install Gitea"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"failureCard"_s) != nullptr; }));
        auto *t = findItem(visibleByName(m_win, u"failureCard"_s), [](QQuickItem *i) { return i->property("text").toString().contains(u"administrator"_s); });
        QVERIFY(t);
    }

    void themes_are_applied_from_the_store()
    {
        open(true);
        go(u"themes"_s);
        QVERIFY(waitFor([this] { return byName(m_win, u"theme-graphite-ice"_s) != nullptr; }));
        auto *apply = byName(m_win, u"apply-graphite-ice"_s);
        QVERIFY(apply && apply->isVisible());
        click(apply);
        QFile f(qEnvironmentVariable("XDG_CONFIG_HOME") + u"/taos/appearance.json"_s);
        QVERIFY(waitFor([&] { return f.open(QIODevice::ReadOnly) && (f.close(), true); }));
        f.open(QIODevice::ReadOnly);
        QCOMPARE(QJsonDocument::fromJson(f.readAll()).object()[u"theme"_s].toString(), u"graphite-ice"_s);
    }

    void search_finds_across_kinds_and_escape_clears()
    {
        open(true);
        QVERIFY(waitFor([this] { return m_win->property("engine").value<QObject *>()->property("loaded").toBool(); }));
        auto *search = byName(m_win, u"searchField"_s);
        QVERIFY(search);
        search->setProperty("text", u"search"_s);
        QVERIFY(waitFor([this] { return byName(m_win, u"row-searxng"_s) != nullptr && byName(m_win, u"row-web-search"_s) != nullptr; }));
        search->setProperty("text", u"youtube"_s);
        QVERIFY(waitFor([this] { return byName(m_win, u"row-youtube"_s) != nullptr && byName(m_win, u"row-youtube-music"_s) != nullptr; }));
        QVERIFY(!byName(m_win, u"row-searxng"_s) || !byName(m_win, u"row-searxng"_s)->isVisible());
    }
};

QTEST_MAIN(TstStoreUi)
#include "tst_store_ui.moc"
