// SPDX-License-Identifier: AGPL-3.0-or-later
// The web apps engine: add, list, launch line, clone, rename, remove, clear data, refuse while running, URL
// validation, path safety, atomic index, and the CLI. A fake browser stands in for Chromium; no network, and
// HOME and XDG_DATA_HOME are never touched (every test uses a temporary root).
#include <QBuffer>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>

#include "webapp.h"

using namespace Qt::StringLiterals;
using namespace TaosWebapp;

namespace {

QByteArray pngBytes(int size, QRgb c = qRgb(40, 120, 200))
{
    QImage img(size, size, QImage::Format_RGB32);
    img.fill(c);
    QByteArray b;
    QBuffer buf(&b);
    buf.open(QIODevice::WriteOnly);
    img.save(&buf, "PNG");
    return b;
}

QByteArray readAll(const QString &p)
{
    QFile f(p);
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}

void writeFile(const QString &p, const QByteArray &d)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(d);
}

} // namespace

class TstWebapp : public QObject
{
    Q_OBJECT
    QTemporaryDir m_tmp;
    QString m_root, m_apps, m_log, m_stop;
    QMap<QString, FetchResult> m_net;   // url -> response (the fake network)
    QStringList m_fetched;

    Options opts()
    {
        Options o;
        o.root = m_root;
        o.applicationsDir = m_apps;
        o.execName = u"taos-webapp"_s;
        o.colorScheme = [] { return QString(); };   // never the machine's own theme or portals
        o.portalPresent = [] { return true; };
        o.fetcher = [this](const QUrl &u) {
            m_fetched << u.toString();
            return m_net.value(u.toString(), FetchResult());
        };
        return o;
    }
    void stopBrowsers()
    {
        writeFile(m_stop, "stop");
        QTest::qWait(400);
        QFile::remove(m_stop);
    }
    bool waitRunning(Engine &e, const QString &id, bool want = true)
    {
        for (int i = 0; i < 60; ++i) {
            if (e.isRunning(id) == want)
                return true;
            QTest::qWait(100);
        }
        return false;
    }

private Q_SLOTS:
    void init()
    {
        const QString base = m_tmp.path() + u'/' + QString::number(QRandomGenerator::global()->generate());
        m_root = base + u"/data/taos/webapps"_s;
        m_apps = base + u"/data/applications"_s;
        m_log = base + u"/browser.log"_s;
        m_stop = base + u"/stop"_s;
        QDir().mkpath(base);
        m_net.clear();
        m_fetched.clear();
        qputenv("TAOS_WEBAPP_BROWSER", QByteArray(FAKE_BROWSER));
        qputenv("FAKE_BROWSER_LOG", m_log.toUtf8());
        qputenv("FAKE_BROWSER_STOP", m_stop.toUtf8());
        qputenv("TAOS_WEBAPP_OZONE", QByteArray());
        qunsetenv("TAOS_WEBAPP_OZONE");
    }
    void cleanup() { stopBrowsers(); }

    void url_validation_table()
    {
        QString err;
        QCOMPARE(Engine::normalizeUrl(u"https://www.youtube.com"_s, false, &err).toString(), u"https://www.youtube.com"_s);
        QCOMPARE(Engine::normalizeUrl(u"youtube.com"_s, false, &err).toString(), u"https://youtube.com"_s);   // a bare host is https
        QCOMPARE(Engine::normalizeUrl(u"  HTTPS://Example.COM/a?b=1  "_s, false, &err).toString(), u"https://example.com/a?b=1"_s);
        QVERIFY(!Engine::normalizeUrl(u"http://example.com"_s, false, &err).isValid());
        QVERIFY(err.contains(u"https"_s));
        QCOMPARE(Engine::normalizeUrl(u"http://192.168.1.5:8080"_s, true, &err).scheme(), u"http"_s);
        for (const QString &bad : {u"file:///etc/passwd"_s, u"javascript:alert(1)"_s, u"data:text/html,<b>x</b>"_s, u"ftp://example.com"_s,
                                   u"chrome://settings"_s, u"about:blank"_s, u"blob:https://x"_s, u"view-source:https://x"_s, u""_s,
                                   u"https://"_s, u"https://user:pw@example.com"_s, u"two words.com"_s, u"mailto:a@b.c"_s}) {
            QVERIFY2(!Engine::normalizeUrl(bad, true, &err).isValid(), qPrintable(bad));
            QVERIFY(!err.isEmpty());
        }
    }

    void add_writes_registry_manifest_icon_and_desktop_entry()
    {
        Engine e(opts());
        Result r = e.add({u"https://www.youtube.com"_s, u"YouTube"_s, {}, u"youtube"_s});
        QVERIFY2(r.ok, qPrintable(r.error));
        const QString id = r.data[u"id"_s].toString();
        QVERIFY(Engine::validId(id));
        QCOMPARE(id.size(), 10);
        // registry
        const QJsonObject idx = QJsonDocument::fromJson(readAll(m_root + u"/index.json"_s)).object();
        QCOMPARE(idx[u"version"_s].toInt(), 1);
        QCOMPARE(idx[u"instances"_s].toArray().size(), 1);
        QCOMPARE(e.list().size(), 1);
        QCOMPARE(e.list().first().source, u"youtube"_s);
        // own folders and files
        QVERIFY(QFileInfo(m_root + u'/' + id + u"/profile"_s).isDir());
        QVERIFY(QFileInfo(m_root + u'/' + id + u"/manifest.json"_s).isFile());
        QVERIFY(QFileInfo(m_root + u'/' + id + u"/icon.svg"_s).isFile());   // no network: a monogram
        QVERIFY(r.data[u"icon_fallback"_s].toBool());
        QVERIFY(readAll(m_root + u'/' + id + u"/icon.svg"_s).contains(">Y<"));
        // the launcher entry, as the dock and launcher read it
        const QString dt = QString::fromUtf8(readAll(m_apps + u"/taos-webapp-"_s + id + u".desktop"_s));
        QVERIFY(dt.contains(u"Name=YouTube\n"_s));
        QVERIFY(dt.contains(u"Exec=taos-webapp launch %1\n"_s.arg(id)));
        QVERIFY(dt.contains(u"StartupWMClass=chrome-www.youtube.com__-%1\n"_s.arg(id)));   // the id Chromium really gives the window
        QVERIFY(dt.contains(u"X-taOS-WebApp=%1\n"_s.arg(id)));
        QVERIFY(dt.contains(u"Icon=%1/%2/icon.svg\n"_s.arg(m_root, id)));
        QVERIFY(dt.contains(u"Actions=clone;clear-data;"_s));
        QVERIFY(dt.contains(u"[Desktop Action clone]\nName=Clone (keeps the sign-in)\nExec=taos-webapp clone %1"_s.arg(id)));
        QVERIFY(dt.contains(u"[Desktop Action clear-data]"_s));
        // no stray temp file from the atomic writes
        for (const QString &f : QDir(m_root).entryList(QDir::Files | QDir::Hidden))
            QVERIFY2(!f.contains(u"tmp"_s, Qt::CaseInsensitive) && !f.endsWith(u".new"_s), qPrintable(f));
    }

    void name_defaults_to_the_host()
    {
        Engine e(opts());
        Result r = e.add({u"https://www.example.org/app"_s});
        QVERIFY(r.ok);
        QCOMPARE(r.data[u"name"_s].toString(), u"example.org"_s);
    }

    void refuses_bad_urls_and_creates_nothing()
    {
        Engine e(opts());
        Result r = e.add({u"javascript:alert(1)"_s});
        QVERIFY(!r.ok);
        QCOMPARE(r.code, u"invalid_url"_s);
        r = e.add({u"http://example.com"_s});
        QCOMPARE(r.code, u"invalid_url"_s);
        QVERIFY(e.list().isEmpty());
        QVERIFY(!QFileInfo::exists(m_root + u"/index.json"_s));
        AddRequest ok{u"http://example.com"_s};
        ok.allowHttp = true;
        QVERIFY(e.add(ok).ok);
    }

    void icon_auto_takes_the_largest_icon_from_the_manifest()
    {
        const QString page = u"https://site.example/"_s;
        auto resp = [](const QByteArray &d, const QString &ct) { FetchResult f; f.ok = true; f.data = d; f.contentType = ct; return f; };
        m_net[page] = resp("<html><head><title> Site Title </title>"
                           "<link rel=\"icon\" href=\"/small.png\" sizes=\"32x32\">"
                           "<link rel='manifest' href='/app.webmanifest'></head></html>", u"text/html"_s);
        m_net[u"https://site.example/app.webmanifest"_s] =
            resp("{\"name\":\"Site\",\"icons\":[{\"src\":\"/i/192.png\",\"sizes\":\"192x192\"},{\"src\":\"/i/512.png\",\"sizes\":\"512x512\",\"type\":\"image/png\"}]}", u"application/json"_s);
        m_net[u"https://site.example/small.png"_s] = resp(pngBytes(32), u"image/png"_s);
        m_net[u"https://site.example/i/192.png"_s] = resp(pngBytes(192), u"image/png"_s);
        m_net[u"https://site.example/i/512.png"_s] = resp(pngBytes(512), u"image/png"_s);
        Engine e(opts());
        Result r = e.add({page, {}, u"auto"_s});
        QVERIFY2(r.ok, qPrintable(r.error));
        QVERIFY(!r.data[u"icon_fallback"_s].toBool());
        QCOMPARE(r.data[u"name"_s].toString(), u"Site Title"_s);     // no name given: the page title
        QVERIFY(r.data[u"icon"_s].toString().endsWith(u"/icon.png"_s));
        const QImage got(r.data[u"icon"_s].toString());
        QVERIFY(!got.isNull());
        QCOMPARE(got.width(), 256);                                  // the 512 icon, scaled down to 256
        QVERIFY(m_fetched.contains(u"https://site.example/i/512.png"_s));
        QVERIFY(!m_fetched.contains(u"https://site.example/small.png"_s));   // the first working one wins
    }

    void icon_auto_without_network_falls_back_to_a_monogram()
    {
        Engine e(opts());   // every fetch fails
        Result r = e.add({u"https://offline.example"_s, u"Offline Site"_s, u"auto"_s});
        QVERIFY(r.ok);
        QVERIFY(r.data[u"icon_fallback"_s].toBool());
        QVERIFY(r.data[u"icon"_s].toString().endsWith(u"/icon.svg"_s));
        QVERIFY(readAll(r.data[u"icon"_s].toString()).contains(">O<"));
    }

    void icon_from_a_file_and_a_bad_file()
    {
        const QString png = m_tmp.path() + u"/pic.png"_s;
        writeFile(png, pngBytes(64));
        Engine e(opts());
        Result r = e.add({u"https://a.example"_s, u"A"_s, png});
        QVERIFY2(r.ok, qPrintable(r.error));
        QVERIFY(r.data[u"icon"_s].toString().endsWith(u"/icon.png"_s));
        const QString txt = m_tmp.path() + u"/not.png"_s;
        writeFile(txt, "hello");
        r = e.add({u"https://b.example"_s, u"B"_s, txt});
        QCOMPARE(r.code, u"invalid_icon"_s);
        QCOMPARE(e.list().size(), 1);   // the failed add left nothing behind
        QCOMPARE(QDir(m_root).entryList(QDir::Dirs | QDir::NoDotAndDotDot).size(), 1);
    }

    void launch_command_line_environment_and_last_used()
    {
        // what the wrapper's shell.env leaves in the environment: the browser must not inherit it
        struct EnvKeep {
            QList<QPair<QByteArray, QByteArray>> saved;
            ~EnvKeep() { for (const auto &p : saved) { if (p.second.isNull()) qunsetenv(p.first.constData()); else qputenv(p.first.constData(), p.second); } }
            void set(const char *k, const QByteArray &v) { saved.append({QByteArray(k), qEnvironmentVariableIsSet(k) ? qgetenv(k) : QByteArray()}); qputenv(k, v); }
        } keep;
        keep.set("LD_LIBRARY_PATH", "/opt/taos/qt/lib");
        keep.set("QT_PLUGIN_PATH", "/opt/taos/qt/plugins");
        keep.set("QML_IMPORT_PATH", "/opt/taos/qml");
        keep.set("MESA_LOADER_DRIVER_OVERRIDE", "keep");
        Engine e(opts());
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        writeFile(m_root + u'/' + id + u"/env"_s, "# pinned for this app\nTAOS_FAKE_PIN=gpu1\nbad line\n");
        Result r = e.launch(id);
        QVERIFY2(r.ok, qPrintable(r.error));
        QVERIFY(waitRunning(e, id));
        QString log;
        for (int i = 0; i < 40 && !log.contains(u"ARGS:"_s); ++i) {
            QTest::qWait(50);
            log = QString::fromUtf8(readAll(m_log));
        }
        const QString appId = u"chrome-www.youtube.com__-"_s + id;
        QVERIFY(log.contains(u"--profile-directory=%1"_s.arg(id)));
        QVERIFY2(log.contains(u"--app=https://www.youtube.com"_s), qPrintable(log));
        QVERIFY(log.contains(u"--user-data-dir=%1/%2/profile"_s.arg(m_root, id)));
        QVERIFY(log.contains(u"--class=%1"_s.arg(appId)));
        QVERIFY(log.contains(u"--name=%1"_s.arg(appId)));
        QVERIFY(log.contains(u"--ozone-platform=wayland"_s));
        QVERIFY(log.contains(u"--no-first-run"_s));
        QVERIFY(log.contains(u"--no-default-browser-check"_s));
        QVERIFY(log.contains(u"ENV_TEST:gpu1"_s));
        QVERIFY2(log.contains(u"ENV_LD:\n"_s) && log.contains(u"ENV_QT:\n"_s) && log.contains(u"ENV_GPU:keep"_s), qPrintable(log));   // Qt environment dropped, GPU one kept                    // the per instance env file
        QVERIFY(e.list().first().lastUsed.isValid());
        QVERIFY(e.info(id).data[u"running"_s].toBool());
    }

    void launch_carries_the_dark_hint_only_when_dark_and_no_portal()
    {
        auto launchLog = [&](const QString &scheme, bool portal) -> QStringList {
            QFile::remove(m_log);
            Options o = opts();
            o.colorScheme = [scheme] { return scheme; };
            o.portalPresent = [portal] { return portal; };
            Engine e(o);
            const QString id = e.add({u"https://a.example"_s, u"A"_s}).data[u"id"_s].toString();
            const Result r = e.launch(id);
            if (!r.ok)
                return {r.error};
            const QStringList args = r.data[u"arguments"_s].toVariant().toStringList();
            stopBrowsers();
            return args;
        };
        QVERIFY(launchLog(u"dark"_s, false).contains(u"--force-dark-mode"_s));
        QVERIFY(!launchLog(u"dark"_s, true).contains(u"--force-dark-mode"_s));      // a portal tells Chromium, live
        QVERIFY(!launchLog(u"light"_s, false).contains(u"--force-dark-mode"_s));
        QVERIFY(!launchLog(QString(), false).contains(u"--force-dark-mode"_s));     // unknown: no hint
        QVERIFY(!launchLog(u"dark"_s, false).contains(u"--enable-features=WebContentsForceDark"_s));
    }

    void launch_without_a_browser_says_so()
    {
        qputenv("TAOS_WEBAPP_BROWSER", "/nonexistent/chromium");
        Engine e(opts());
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        Result r = e.launch(id);
        QVERIFY(!r.ok);
        QCOMPARE(r.code, u"no_browser"_s);
        QVERIFY(r.error.contains(u"No browser engine installed"_s));
        QVERIFY(r.error.contains(u"chromium"_s));
        r = e.doctor();
        QCOMPARE(r.code, u"no_browser"_s);
        QVERIFY(r.error.contains(u"No browser engine installed"_s));
        QVERIFY(!e.info(id).data[u"running"_s].toBool());
    }

    void doctor_finds_the_browser()
    {
        Engine e(opts());
        Result r = e.doctor();
        QVERIFY2(r.ok, qPrintable(r.error));
        QVERIFY(r.data[u"chosen"_s].toString().endsWith(u"fakebrowser.sh"_s));
        QCOMPARE(r.data[u"browsers"_s].toArray().first().toObject()[u"version"_s].toString(), u"Fake Chromium 1.2.3"_s);
        // the cheap check a window polls with never runs the browser
        const Result quick = e.doctor(false);
        QVERIFY(quick.ok);
        QVERIFY(!quick.data[u"browsers"_s].toArray().first().toObject().contains(u"version"_s));
    }

    void refuses_remove_clone_and_clear_while_running()
    {
        Engine e(opts());
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        QVERIFY(e.launch(id).ok);
        QVERIFY(waitRunning(e, id));
        Result r = e.remove(id);
        QCOMPARE(r.code, u"running"_s);
        QVERIFY(r.error.contains(u"Close its window"_s));
        QCOMPARE(e.clone(id).code, u"running"_s);
        QCOMPARE(e.clearData(id).code, u"running"_s);
        QCOMPARE(e.list().size(), 1);
        QVERIFY(QFileInfo::exists(e.desktopFilePath(id)));
        // rename is harmless while open
        QVERIFY(e.rename(id, u"Tube"_s).ok);
        // force closes it and removes
        r = e.remove(id, false, true);
        QVERIFY2(r.ok, qPrintable(r.error));
        QVERIFY(e.list().isEmpty());
        QVERIFY(!QFileInfo::exists(m_root + u'/' + id));
        QVERIFY(!QFileInfo::exists(e.desktopFilePath(id)));
    }

    void a_stale_lock_does_not_count_as_running()
    {
        Engine e(opts());
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        QVERIFY(QFile::link(u"somehost-999999999"_s, m_root + u'/' + id + u"/profile/SingletonLock"_s));
        QVERIFY(!e.isRunning(id));
        QVERIFY(e.remove(id).ok);
    }

    void clone_copies_the_profile_so_the_sign_in_comes_along()
    {
        Engine e(opts());
        const QString id = e.add({u"https://mail.example"_s, u"Mail"_s}).data[u"id"_s].toString();
        const QString prof = m_root + u'/' + id + u"/profile"_s;
        writeFile(prof + u"/Default/Cookies"_s, "session=secret-token");
        writeFile(prof + u"/Default/Local Storage/leveldb/000003.log"_s, "ls");
        writeFile(prof + u"/Default/Cache/Cache_Data/f_1"_s, "cached");
        writeFile(prof + u"/Default/Code Cache/js/x"_s, "code");
        QVERIFY(QFile::link(u"host-1"_s, prof + u"/SingletonLock"_s));   // a stale lock must not be copied
        Result r = e.clone(id);
        QVERIFY2(r.ok, qPrintable(r.error));
        const QString nid = r.data[u"id"_s].toString();
        QVERIFY(nid != id);
        QVERIFY(Engine::validId(nid));
        QCOMPARE(r.data[u"name"_s].toString(), u"Mail copy"_s);
        QCOMPARE(r.data[u"url"_s].toString(), u"https://mail.example"_s);
        const QString nprof = m_root + u'/' + nid + u"/profile"_s;
        QCOMPARE(readAll(nprof + u"/Default/Cookies"_s), QByteArray("session=secret-token"));
        QCOMPARE(readAll(nprof + u"/Default/Local Storage/leveldb/000003.log"_s), QByteArray("ls"));
        QVERIFY(!QFileInfo::exists(nprof + u"/Default/Cache"_s));
        QVERIFY(!QFileInfo::exists(nprof + u"/Default/Code Cache"_s));
        QVERIFY(!QFileInfo(nprof + u"/SingletonLock"_s).isSymLink());
        // independent: changing the clone leaves the original alone
        writeFile(nprof + u"/Default/Cookies"_s, "other-account");
        QCOMPARE(readAll(prof + u"/Default/Cookies"_s), QByteArray("session=secret-token"));
        // own launcher entry and its own window class
        const QString dt = QString::fromUtf8(readAll(e.desktopFilePath(nid)));
        QVERIFY(dt.contains(u"StartupWMClass=chrome-mail.example__-%1"_s.arg(nid)));
        QVERIFY(dt.contains(u"Name=Mail copy"_s));
        QVERIFY(QFileInfo(r.data[u"icon"_s].toString()).isFile());
        QVERIFY(r.data[u"icon"_s].toString().startsWith(m_root + u'/' + nid));
        QCOMPARE(e.list().size(), 2);
        QCOMPARE(e.clone(id, u"Mail (work)"_s).data[u"name"_s].toString(), u"Mail (work)"_s);
        QVERIFY(!QDir(m_root).entryList(QDir::Dirs).filter(u"partial"_s).size());
    }

    void expected_app_id_follows_chromiums_naming()
    {
        auto id = [](const char *url, const char *profile) { return expectedAppId(QUrl(QString::fromLatin1(url)), QString::fromLatin1(profile)); };
        QCOMPARE(id("https://www.youtube.com", "Default"), u"chrome-www.youtube.com__-Default"_s);       // measured
        QCOMPARE(id("https://www.youtube.com/", "Default"), u"chrome-www.youtube.com__-Default"_s);      // no path = "/"
        QCOMPARE(id("https://www.youtube.com/", "k3j9x0a1bc"), u"chrome-www.youtube.com__-k3j9x0a1bc"_s);
        QCOMPARE(id("https://WWW.YouTube.com", "x"), u"chrome-www.youtube.com__-x"_s);                  // host lower case
        QCOMPARE(id("https://example.org:8443/", "x"), u"chrome-example.org__-x"_s);                    // the port does not take part
        QCOMPARE(id("https://github.com/jay", "x"), u"chrome-github.com__jay-x"_s);
        QCOMPARE(id("https://github.com/jay/", "x"), u"chrome-github.com__jay_-x"_s);                   // a trailing slash stays
        QCOMPARE(id("https://web.telegram.org/a/", "x"), u"chrome-web.telegram.org__a_-x"_s);
        QCOMPARE(id("https://www.google.com/maps?hl=en#x", "x"), u"chrome-www.google.com__maps-x"_s);   // no query, no fragment
        QCOMPARE(id("https://a.example/p:q|r", "x"), u"chrome-a.example__p_q%7Cr-x"_s);                 // the path is as a URL holds it (| is %7C); : is a character a file name cannot hold
        QCOMPARE(id("https://a.example", ""), u"chrome-a.example__-Default"_s);
    }

    void legacy_instances_are_migrated_and_a_second_one_of_the_same_window_id_does_not_start()
    {
        Engine e(opts());
        // two instances as the first version wrote them: no profile_dir, the old window class
        QJsonArray arr;
        for (const char *id : {"aaaaaaaaaa", "bbbbbbbbbb"}) {
            QJsonObject o{{u"id"_s, QString::fromLatin1(id)}, {u"name"_s, u"YT "_s + QString::fromLatin1(id)}, {u"url"_s, u"https://www.youtube.com"_s},
                          {u"icon"_s, QString()}, {u"source"_s, u"youtube"_s}, {u"browser"_s, u"auto"_s}};
            arr.append(o);
            QDir().mkpath(m_root + u'/' + QString::fromLatin1(id) + u"/profile"_s);
            writeFile(m_apps + u"/taos-webapp-"_s + QString::fromLatin1(id) + u".desktop"_s,
                      "[Desktop Entry]\nType=Application\nName=Old\nExec=taos-webapp launch x\nStartupWMClass=taos-webapp-old\n");
        }
        writeFile(m_root + u"/index.json"_s, QJsonDocument(QJsonObject{{u"version"_s, 1}, {u"instances"_s, arr}}).toJson());
        QCOMPARE(e.list().first().profile, u"Default"_s);
        QCOMPARE(e.list().first().appId(), u"chrome-www.youtube.com__-Default"_s);
        e.syncLaunchers();
        QVERIFY(QString::fromUtf8(readAll(e.desktopFilePath(u"aaaaaaaaaa"_s))).contains(u"StartupWMClass=chrome-www.youtube.com__-Default\n"_s));
        QVERIFY(e.launch(u"aaaaaaaaaa"_s).ok);
        QVERIFY(waitRunning(e, u"aaaaaaaaaa"_s));
        const Result r = e.launch(u"bbbbbbbbbb"_s);
        QCOMPARE(r.code, u"collision"_s);
        QVERIFY(r.error.contains(u"Use Clone"_s));
        stopBrowsers();
        QVERIFY(waitRunning(e, u"aaaaaaaaaa"_s, false));
        // a clone of a legacy instance has its own profile and window id, so both can be open
        const Result c = e.clone(u"aaaaaaaaaa"_s);
        QVERIFY2(c.ok, qPrintable(c.error));
        const QString nid = c.data[u"id"_s].toString();
        QCOMPARE(c.data[u"profile_dir"_s].toString(), nid);
        QVERIFY(c.data[u"app_id"_s].toString() != u"chrome-www.youtube.com__-Default"_s);
        QVERIFY(e.launch(u"aaaaaaaaaa"_s).ok);
        QVERIFY(waitRunning(e, u"aaaaaaaaaa"_s));
        QVERIFY2(e.launch(nid).ok, "a clone starts next to its original");
    }

    void a_clone_renames_the_profile_directory_and_patches_local_state()
    {
        Engine e(opts());
        const QString id = e.add({u"https://mail.example"_s, u"Mail"_s}).data[u"id"_s].toString();
        const QString prof = m_root + u'/' + id + u"/profile"_s;
        writeFile(prof + u'/' + id + u"/Cookies"_s, "tok");
        writeFile(prof + u"/Local State"_s,
                  QJsonDocument(QJsonObject{{u"profile"_s, QJsonObject{{u"info_cache"_s, QJsonObject{{id, QJsonObject{{u"name"_s, u"Mail"_s}}}}},
                                                                      {u"last_used"_s, id}, {u"last_active_profiles"_s, QJsonArray{id}},
                                                                      {u"profiles_order"_s, QJsonArray{id}}}},
                                            {u"other"_s, 1}}).toJson());
        const Result r = e.clone(id);
        QVERIFY2(r.ok, qPrintable(r.error));
        const QString nid = r.data[u"id"_s].toString();
        const QString np = m_root + u'/' + nid + u"/profile"_s;
        QCOMPARE(readAll(np + u'/' + nid + u"/Cookies"_s), QByteArray("tok"));
        QVERIFY(!QFileInfo::exists(np + u'/' + id));
        const QJsonObject ls = QJsonDocument::fromJson(readAll(np + u"/Local State"_s)).object();
        const QJsonObject p = ls[u"profile"_s].toObject();
        QVERIFY(p[u"info_cache"_s].toObject().contains(nid));
        QVERIFY(!p[u"info_cache"_s].toObject().contains(id));
        QCOMPARE(p[u"last_used"_s].toString(), nid);
        QCOMPARE(p[u"last_active_profiles"_s].toArray().first().toString(), nid);
        QCOMPARE(p[u"profiles_order"_s].toArray().first().toString(), nid);
        QCOMPARE(ls[u"other"_s].toInt(), 1);
        QCOMPARE(readAll(prof + u'/' + id + u"/Cookies"_s), QByteArray("tok"));   // the original is untouched
    }

    void own_title_bar_is_seeded_merged_repaired_and_carried()
    {
        auto customFrame = [&](const QString &prefs) {
            return QJsonDocument::fromJson(readAll(prefs)).object()[u"browser"_s].toObject()[u"custom_chrome_frame"_s].toBool();
        };
        Engine e(opts());
        // a new instance: seeded before its first launch, in <user data dir>/<instance id>/Preferences
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        const QString prefs = m_root + u'/' + id + u"/profile/"_s + id + u"/Preferences"_s;
        QVERIFY(customFrame(prefs));
        // an existing Preferences with other keys keeps them
        writeFile(prefs, "{\"browser\":{\"custom_chrome_frame\":false,\"window_placement\":{\"left\":5}},\"profile\":{\"name\":\"x\"},\"zoom\":1}");
        QVERIFY(e.ensureOwnTitleBar(id).ok);
        const QJsonObject o = QJsonDocument::fromJson(readAll(prefs)).object();
        QVERIFY(o[u"browser"_s].toObject()[u"custom_chrome_frame"_s].toBool());
        QCOMPARE(o[u"browser"_s].toObject()[u"window_placement"_s].toObject()[u"left"_s].toInt(), 5);
        QCOMPARE(o[u"profile"_s].toObject()[u"name"_s].toString(), u"x"_s);
        QCOMPARE(o[u"zoom"_s].toInt(), 1);
        // a file that is not JSON is left alone, with a sentence
        writeFile(prefs, "not json");
        QCOMPARE(e.ensureOwnTitleBar(id).code, u"io"_s);
        QCOMPARE(readAll(prefs), QByteArray("not json"));
        // never while it runs
        QVERIFY(e.launch(id).ok);
        QVERIFY(waitRunning(e, id));
        writeFile(prefs, "{\"x\":1}");
        QCOMPARE(e.ensureOwnTitleBar(id).code, u"running"_s);
        QCOMPARE(readAll(prefs), QByteArray("{\"x\":1}"));
        stopBrowsers();
        QVERIFY(waitRunning(e, id, false));
        // the next launch repairs it
        QVERIFY(e.launch(id).ok);
        QVERIFY(waitRunning(e, id));
        QVERIFY(customFrame(prefs));
        stopBrowsers();
        QVERIFY(waitRunning(e, id, false));
        // clone carries it (to the renamed profile directory)
        const QString nid = e.clone(id).data[u"id"_s].toString();
        QVERIFY(customFrame(m_root + u'/' + nid + u"/profile/"_s + nid + u"/Preferences"_s));
    }

    void a_legacy_default_profile_is_repaired_at_launch()
    {
        Engine e(opts());
        QJsonObject o{{u"id"_s, u"aaaaaaaaaa"_s}, {u"name"_s, u"YouTube"_s}, {u"url"_s, u"https://www.youtube.com"_s}, {u"source"_s, u"youtube"_s}, {u"browser"_s, u"auto"_s}};
        writeFile(m_root + u"/index.json"_s, QJsonDocument(QJsonObject{{u"version"_s, 1}, {u"instances"_s, QJsonArray{o}}}).toJson());
        const QString prefs = m_root + u"/aaaaaaaaaa/profile/Default/Preferences"_s;
        writeFile(prefs, "{\"intl\":{\"accept_languages\":\"en\"}}");
        QVERIFY(e.launch(u"aaaaaaaaaa"_s).ok);
        const QJsonObject p = QJsonDocument::fromJson(readAll(prefs)).object();
        QVERIFY(p[u"browser"_s].toObject()[u"custom_chrome_frame"_s].toBool());
        QCOMPARE(p[u"intl"_s].toObject()[u"accept_languages"_s].toString(), u"en"_s);
        // and with no Preferences at all
        stopBrowsers();
        QVERIFY(waitRunning(e, u"aaaaaaaaaa"_s, false));
        QFile::remove(prefs);
        QVERIFY(e.launch(u"aaaaaaaaaa"_s).ok);
        QVERIFY(QJsonDocument::fromJson(readAll(prefs)).object()[u"browser"_s].toObject()[u"custom_chrome_frame"_s].toBool());
    }

    void rename_updates_the_registry_and_the_desktop_entry()
    {
        Engine e(opts());
        const QString id = e.add({u"https://www.youtube.com"_s, u"YouTube"_s}).data[u"id"_s].toString();
        QVERIFY(e.rename(id, u"YouTube (kids)\nExec=evil"_s).ok);
        QCOMPARE(e.list().first().name, u"YouTube (kids) Exec=evil"_s);   // control characters never reach the .desktop file
        const QString dt = QString::fromUtf8(readAll(e.desktopFilePath(id)));
        QVERIFY(dt.contains(u"Name=YouTube (kids) Exec=evil\n"_s));
        QCOMPARE(dt.count(u"\nExec=taos-webapp launch"_s), 1);
        QVERIFY(QString::fromUtf8(readAll(m_root + u'/' + id + u"/manifest.json"_s)).contains(u"YouTube (kids)"_s));
        QCOMPARE(e.rename(id, u"   "_s).code, u"invalid"_s);
        QCOMPARE(e.rename(u"nope000000"_s, u"x"_s).code, u"not_found"_s);
    }

    void remove_and_keep_data()
    {
        Engine e(opts());
        const QString a = e.add({u"https://a.example"_s, u"A"_s}).data[u"id"_s].toString();
        const QString b = e.add({u"https://b.example"_s, u"B"_s}).data[u"id"_s].toString();
        writeFile(m_root + u'/' + a + u"/profile/Default/Cookies"_s, "c");
        QVERIFY(e.remove(a, true).ok);   // keep data
        QVERIFY(QFileInfo::exists(m_root + u'/' + a + u"/profile/Default/Cookies"_s));
        QVERIFY(!QFileInfo::exists(e.desktopFilePath(a)));
        QCOMPARE(e.list().size(), 1);
        QVERIFY(e.doctor().data[u"orphan_data"_s].toArray().contains(a));
        QVERIFY(e.remove(b).ok);
        QVERIFY(!QFileInfo::exists(m_root + u'/' + b));
        QVERIFY(e.list().isEmpty());
        QCOMPARE(e.remove(b).code, u"not_found"_s);
    }

    void clear_data_empties_the_profile_and_keeps_the_instance()
    {
        Engine e(opts());
        const QString id = e.add({u"https://a.example"_s, u"A"_s}).data[u"id"_s].toString();
        writeFile(m_root + u'/' + id + u"/profile/Default/Cookies"_s, "c");
        QVERIFY(e.clearData(id).ok);
        QVERIFY(QFileInfo(m_root + u'/' + id + u"/profile"_s).isDir());
        QVERIFY(QDir(m_root + u'/' + id + u"/profile"_s).entryList(QDir::AllEntries | QDir::NoDotAndDotDot).isEmpty());
        QCOMPARE(e.list().size(), 1);
        QVERIFY(QFileInfo(e.desktopFilePath(id)).isFile());
    }

    void ids_are_generated_and_paths_stay_inside_the_root()
    {
        Engine e(opts());
        for (const QString &bad : {u"../etc"_s, u"a/b"_s, u".."_s, u""_s, u"ABCDEFGHIJ"_s, u"short"_s, u"abcdefghij/../.."_s}) {
            QVERIFY2(!Engine::validId(bad), qPrintable(bad));
            QVERIFY(e.instanceDir(bad).isEmpty());
            QVERIFY(e.profileDir(bad).isEmpty());
            QVERIFY(e.desktopFilePath(bad).isEmpty());
            QCOMPARE(e.remove(bad).code, u"invalid_id"_s);
            QCOMPARE(e.clearData(bad).code, u"invalid_id"_s);
            QCOMPARE(e.clone(bad).code, u"invalid_id"_s);
            QCOMPARE(e.launch(bad).code, u"invalid_id"_s);
        }
        // the name a person types never becomes part of a path
        Result r = e.add({u"https://a.example"_s, u"../../evil"_s});
        QVERIFY(r.ok);
        const QString id = r.data[u"id"_s].toString();
        QVERIFY(e.instanceDir(id).startsWith(m_root + u'/'));
        QVERIFY(!QFileInfo::exists(m_tmp.path() + u"/evil"_s));
        // two instances of one site get two folders
        const QString id2 = e.add({u"https://a.example"_s, u"../../evil"_s}).data[u"id"_s].toString();
        QVERIFY(id != id2);
    }

    void a_symlinked_profile_is_never_followed()
    {
        Engine e(opts());
        const QString id = e.add({u"https://a.example"_s, u"A"_s}).data[u"id"_s].toString();
        const QString outside = m_tmp.path() + u"/outside"_s;
        writeFile(outside + u"/precious.txt"_s, "keep me");
        QDir(m_root + u'/' + id + u"/profile"_s).removeRecursively();
        QVERIFY(QFile::link(outside, m_root + u'/' + id + u"/profile"_s));
        QVERIFY(e.clearData(id).ok);
        QCOMPARE(readAll(outside + u"/precious.txt"_s), QByteArray("keep me"));
        // and the whole instance folder as a link
        const QString id2 = e.add({u"https://b.example"_s, u"B"_s}).data[u"id"_s].toString();
        QDir(m_root + u'/' + id2).removeRecursively();
        QVERIFY(QFile::link(outside, m_root + u'/' + id2));
        QVERIFY(e.remove(id2).ok);
        QCOMPARE(readAll(outside + u"/precious.txt"_s), QByteArray("keep me"));
    }

    void two_engines_keep_both_entries()
    {
        Engine e1(opts()), e2(opts());
        QVERIFY(e1.add({u"https://a.example"_s, u"A"_s}).ok);
        QVERIFY(e2.add({u"https://b.example"_s, u"B"_s}).ok);
        QVERIFY(e1.add({u"https://c.example"_s, u"C"_s}).ok);
        QCOMPARE(e1.list().size(), 3);
        QCOMPARE(e2.list().size(), 3);
    }

    void info_reports_size_last_used_and_readable_permissions()
    {
        Engine e(opts());
        const QString id = e.add({u"https://a.example"_s, u"A"_s}).data[u"id"_s].toString();
        QVERIFY(e.info(id).data[u"permissions"_s].isNull());   // no Preferences yet
        writeFile(m_root + u'/' + id + u"/profile/Default/blob"_s, QByteArray(5000, 'x'));
        writeFile(m_root + u'/' + id + u"/profile/"_s + id + u"/Preferences"_s,
                  "{\"profile\":{\"content_settings\":{\"exceptions\":{\"notifications\":{\"https://a.example:443,*\":{\"setting\":1},\"https://x.example:443,*\":{\"setting\":2}}}}}}");
        const QJsonObject i = e.info(id).data;
        QVERIFY(i[u"size_bytes"_s].toInteger() >= 5000);
        QVERIFY(!i[u"running"_s].toBool());
        QCOMPARE(i[u"permissions"_s].toObject()[u"notifications"_s].toObject()[u"allowed"_s].toArray().size(), 1);
        QCOMPARE(i[u"permissions"_s].toObject()[u"notifications"_s].toObject()[u"blocked"_s].toArray().size(), 1);
        QCOMPARE(i[u"app_id"_s].toString(), u"chrome-a.example__-"_s + id);
        QCOMPARE(e.info(u"nope000000"_s).code, u"not_found"_s);
    }

    void the_cli_does_everything_the_faces_do()
    {
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        const QString data = m_tmp.path() + u"/clidata"_s;
        env.insert(u"XDG_DATA_HOME"_s, data);
        env.insert(u"TAOS_WEBAPP_BROWSER"_s, QString::fromUtf8(FAKE_BROWSER));
        env.insert(u"FAKE_BROWSER_STOP"_s, m_stop);
        env.insert(u"FAKE_BROWSER_LOG"_s, m_log);
        auto run = [&](const QStringList &args, int *code = nullptr) {
            QProcess p;
            p.setProcessEnvironment(env);
            p.start(QString::fromUtf8(WEBAPP_CLI), args);
            [&] { QVERIFY(p.waitForFinished(20000)); }();
            if (code)
                *code = p.exitCode();
            return QString::fromUtf8(p.readAllStandardOutput()) + QString::fromUtf8(p.readAllStandardError());
        };
        int rc = 0;
        QCOMPARE(QJsonDocument::fromJson(run({u"list"_s, u"--json"_s}).toUtf8()).array().size(), 0);
        QString out = run({u"add"_s, u"--url"_s, u"https://www.youtube.com"_s, u"--name"_s, u"YouTube"_s, u"--from-store"_s, u"youtube"_s, u"--json"_s}, &rc);
        QCOMPARE(rc, 0);
        const QJsonObject added = QJsonDocument::fromJson(out.toUtf8()).object();
        const QString id = added[u"id"_s].toString();
        QVERIFY(Engine::validId(id));
        QCOMPARE(added[u"source"_s].toString(), u"youtube"_s);
        QVERIFY(QFileInfo::exists(data + u"/applications/taos-webapp-"_s + id + u".desktop"_s));
        out = run({u"add"_s, u"--url"_s, u"javascript:alert(1)"_s}, &rc);
        QCOMPARE(rc, 1);
        QVERIFY(out.contains(u"https"_s));
        QJsonArray list = QJsonDocument::fromJson(run({u"list"_s, u"--json"_s}).toUtf8()).array();
        QCOMPARE(list.size(), 1);
        QCOMPARE(list[0].toObject()[u"name"_s].toString(), u"YouTube"_s);
        QCOMPARE(QJsonDocument::fromJson(run({u"doctor"_s, u"--json"_s}, &rc).toUtf8()).object()[u"backend"_s].toString(), u"chromium"_s);
        QCOMPARE(rc, 0);
        QVERIFY(run({u"rename"_s, id, u"YT"_s}, &rc).contains(u"YT"_s));
        out = run({u"launch"_s, id}, &rc);
        QCOMPARE(rc, 0);
        bool running = false;
        for (int i = 0; i < 50 && !running; ++i) {
            QTest::qWait(100);
            running = QJsonDocument::fromJson(run({u"info"_s, id, u"--json"_s}).toUtf8()).object()[u"running"_s].toBool();
        }
        QVERIFY(running);
        out = run({u"remove"_s, id}, &rc);
        QCOMPARE(rc, 1);
        QVERIFY(out.contains(u"Close its window"_s));
        const QJsonObject cloneErr = QJsonDocument::fromJson(run({u"clone"_s, id, u"--json"_s}, &rc).toUtf8()).object();
        QCOMPARE(rc, 1);
        QCOMPARE(cloneErr[u"code"_s].toString(), u"running"_s);
        stopBrowsers();
        const QJsonObject cl = QJsonDocument::fromJson(run({u"clone"_s, id, u"--name"_s, u"YT work"_s, u"--json"_s}, &rc).toUtf8()).object();
        QCOMPARE(rc, 0);
        QVERIFY(cl[u"id"_s].toString() != id);
        QCOMPARE(QJsonDocument::fromJson(run({u"list"_s, u"--json"_s}).toUtf8()).array().size(), 2);
        run({u"clear-data"_s, cl[u"id"_s].toString()}, &rc);
        QCOMPARE(rc, 0);
        run({u"remove"_s, id}, &rc);
        QCOMPARE(rc, 0);
        run({u"remove"_s, cl[u"id"_s].toString(), u"--keep-data"_s}, &rc);
        QCOMPARE(rc, 0);
        QCOMPARE(QJsonDocument::fromJson(run({u"list"_s, u"--json"_s}).toUtf8()).array().size(), 0);
        // no browser: exit 3 and a clear sentence, for doctor and launch
        env.insert(u"TAOS_WEBAPP_BROWSER"_s, u"/nonexistent/chromium"_s);
        out = run({u"doctor"_s}, &rc);
        QCOMPARE(rc, 3);
        QVERIFY(out.contains(u"No browser engine installed"_s));
        run({u"add"_s, u"--url"_s, u"https://x.example"_s}, &rc);
        const QString xid = QJsonDocument::fromJson(run({u"list"_s, u"--json"_s}).toUtf8()).array()[0].toObject()[u"id"_s].toString();
        out = run({u"launch"_s, xid}, &rc);
        QCOMPARE(rc, 3);
        QVERIFY(out.contains(u"No browser engine installed"_s));
        run({u"bogus"_s}, &rc);
        QCOMPARE(rc, 2);
    }
};

QTEST_GUILESS_MAIN(TstWebapp)
#include "tst_webapp.moc"
