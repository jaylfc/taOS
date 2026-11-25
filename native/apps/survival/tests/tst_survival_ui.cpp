// SPDX-License-Identifier: AGPL-3.0-or-later
// Survival Studio's window in a QML engine: every section loads against real (fixture) packs with no QML warnings,
// the Library finds and shows an article, the phone layout swaps the sidebar for a tab bar, and the agent panel pops
// out. Run offscreen with a private home: nothing of anyone's data is touched.
#include <QTemporaryDir>

#include "apphost.h"
#include <QWheelEvent>

#include "backend.h"
#include "fakemesh.h"
#include "fixtures.h"

using namespace Taos::Survival;
using namespace Qt::StringLiterals;

namespace {
QStringList g_warnings;
QtMessageHandler g_prev = nullptr;
void handler(QtMsgType t, const QMessageLogContext &c, const QString &m)
{
    if ((t == QtWarningMsg || t == QtCriticalMsg) && (m.contains(u"my.taos.survival"_s) || m.contains(u"Taos/Apps"_s)
        || m.contains(u"TypeError"_s) || m.contains(u"ReferenceError"_s) || m.contains(u"is not defined"_s) || m.contains(u"Unable to assign"_s)))
        g_warnings << m;
    if (g_prev)
        g_prev(t, c, m);
}
QQuickItem *byName(QQuickWindow *w, const QString &n) { return findItem(w->contentItem(), [&](QQuickItem *i) { return i->objectName() == n; }); }
QQuickItem *visibleByName(QQuickWindow *w, const QString &n) { return findItem(w->contentItem(), [&](QQuickItem *i) { return i->objectName() == n && i->isVisible(); }); }
} // namespace

class TstSurvivalUi : public QObject
{
    Q_OBJECT
    QTemporaryDir m_home;
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    SurvivalBackend *m_backend = nullptr;
    FakeEngines::FakeMeshEngine *m_mesh = nullptr;
    FakeEngines::FakeFirmware *m_fw = nullptr;
    QQuickWindow *m_win = nullptr;

    void section(const char *id)
    {
        m_win->setProperty("section", QString::fromLatin1(id));
        QTest::qWait(150);
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
        qputenv("TAOS_SURVIVAL_DIR", (r + u"/survival"_s).toUtf8());
        qputenv("TAOS_REDUCE_MOTION", "1");
        QDir().mkpath(r + u"/survival/packs"_s);
        QVERIFY(Fixture::writeZim(r + u"/survival/packs/lib.zim"_s));
        QVERIFY(Fixture::writePmtiles(r + u"/survival/packs/area.pmtiles"_s));
        // stand-in engines (the real engines' captured JSON), started before the backend looks for them; nothing here
        // opens a radio, writes a setting or flashes a board
        m_mesh = new FakeEngines::FakeMeshEngine(QStringLiteral(MESH_FIXTURES_DIR) + u"/mesh"_s);
        QVERIFY(m_mesh->listen(r + u"/mesh.sock"_s));
        m_fw = new FakeEngines::FakeFirmware(QStringLiteral(MESH_FIXTURES_DIR) + u"/firmware"_s, r + u"/fw"_s);
        qputenv("TAOS_MESH_SOCKET", (r + u"/mesh.sock"_s).toUtf8());
        qputenv("TAOS_FIRMWARE_DIR", (r + u"/fw"_s).toUtf8());
        qputenv("TAOS_FIRMWARE_PORT", QByteArray::number(m_fw->port()));
        qputenv("TAOS_SURVIVAL_NO_SYSTEMCTL", "1");
        g_prev = qInstallMessageHandler(handler);
        m_backend = new SurvivalBackend(r + u"/survival"_s);
        registerSurvivalQml(m_backend);
        m_fake = new FakeHost;
        QVERIFY(m_fake->ok());
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.survival"_s);
        QVERIFY(m_host->waitSignedIn());
        m_win = m_host->load(u"qrc:/my.taos.survival/Main.qml"_s);
        QVERIFY(m_win);
        m_win->resize(1500, 950);
        QTest::qWait(300);
    }
    void cleanupTestCase()
    {
        delete m_host;
        delete m_fake;
        delete m_fw;
        delete m_mesh;
        qInstallMessageHandler(g_prev);
        delete m_backend;
    }

    void library_finds_and_shows_an_article()
    {
        section("library");
        QVERIFY(visibleByName(m_win, u"page-library"_s));
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"librarySearch"_s) != nullptr; }));
        auto *lib = m_backend->library();
        QVERIFY(waitFor([&] { return !lib->property("packId").toString().isEmpty(); }));
        QCOMPARE(lib->property("info").toMap().value(u"title"_s).toString(), u"Test library"_s);
        // typing into the real field drives the real search
        auto *field = byName(m_win, u"librarySearch"_s);
        QVERIFY(field);
        field->setProperty("text", u"bacteria"_s);
        QVERIFY(waitFor([&] { return !lib->property("results").toList().isEmpty(); }, 4000));
        QCOMPARE(lib->property("results").toList().first().toMap().value(u"path"_s).toString(), u"water"_s);
        QMetaObject::invokeMethod(lib, "openArticle", Q_ARG(QString, u"water"_s));
        const QVariantMap art = lib->property("article").toMap();
        QCOMPARE(art.value(u"kind"_s).toString(), u"article"_s);
        QVERIFY(art.value(u"html"_s).toString().contains(u"Boil the water"_s));
        QVERIFY(!art.value(u"html"_s).toString().contains(u"alert"_s));
        // only links into the pack are followed
        QMetaObject::invokeMethod(lib, "openLink", Q_ARG(QString, u"https://evil.example/"_s));
        QCOMPARE(lib->property("article").toMap().value(u"title"_s).toString(), art.value(u"title"_s).toString());
        QMetaObject::invokeMethod(lib, "openLink", Q_ARG(QString, u"zim:local-lib/fire"_s));
        QCOMPARE(lib->property("article").toMap().value(u"title"_s).toString(), u"Fire lighting"_s);
        QTest::qWait(150);
        QVERIFY(!m_win->grabWindow().isNull());
    }
    void packs_page_lists_the_catalogue_and_the_installed_pack()
    {
        section("library");
        m_win->findChild<QObject *>(u"x"_s);   // (nothing: the Packs view is a mode of the Library page)
        auto *page = byName(m_win, u"page-library"_s);
        QVERIFY(page);
        page->setProperty("mode", u"packs"_s);
        QTest::qWait(200);
        QVERIFY(findItem(m_win->contentItem(), [](QQuickItem *i) { return i->objectName() == u"pack-local-lib"_s && i->isVisible(); }));
        QVERIFY(findItem(m_win->contentItem(), [](QQuickItem *i) { return i->objectName() == u"pack-wikipedia-medicine-mini"_s && i->isVisible(); }));
        page->setProperty("mode", u"read"_s);
    }
    void map_opens_the_map_pack_and_taostak_shows_radios_and_messages()
    {
        section("map");
        QVERIFY(visibleByName(m_win, u"page-map"_s));
        auto *map = m_backend->map();
        QVERIFY(waitFor([&] { return map->property("loaded").toBool(); }));
        QCOMPARE(map->property("name").toString(), u"Test map"_s);
        QVERIFY(waitFor([&] { return map->property("indexReady").toBool(); }));
        QVERIFY(visibleByName(m_win, u"mapSearch"_s));
        const auto hits = QVariantList();
        Q_UNUSED(hits);
        QVariantList found;
        QMetaObject::invokeMethod(map, "searchPlaces", Q_RETURN_ARG(QVariantList, found), Q_ARG(QString, u"testv"_s));
        QCOMPARE(found.size(), 1);
        QString id;
        QMetaObject::invokeMethod(map, "addMarker", Q_RETURN_ARG(QString, id), Q_ARG(QString, u"Spring"_s), Q_ARG(QString, QString()), Q_ARG(double, 1.0), Q_ARG(double, 2.0));
        QCOMPARE(map->property("markers").toList().size(), 1);
        QMetaObject::invokeMethod(map, "removeMarker", Q_ARG(QString, id));
        QCOMPARE(map->property("markers").toList().size(), 0);
        QTest::qWait(150);
        QVERIFY(!m_win->grabWindow().isNull());
        section("taostak");
        QVERIFY(visibleByName(m_win, u"page-taostak"_s));
        auto *page = byName(m_win, u"page-taostak"_s);
        for (const char *tab : {"people", "radios", "messages"}) {
            page->setProperty("tab", QString::fromLatin1(tab));
            QTest::qWait(150);
            QVERIFY(!m_win->grabWindow().isNull());
        }
        // no radio is ever claimed without a handshake
        const auto radios = m_backend->radios()->property("radios").toList();
        for (const QVariant &r : radios)
            QVERIFY(r.toMap().value(u"protocol"_s).toString().isEmpty());
    }
    void taostak_every_view_loads_against_the_engines_and_the_guards_hold()
    {
        section("taostak");
        auto *page = byName(m_win, u"page-taostak"_s);
        QVERIFY(page);
        auto *tak = m_backend->tak();
        QVERIFY(waitFor([&] { return tak->property("available").toBool(); }, 8000));
        auto open = [&](const char *view, const QString &arg) {
            QMetaObject::invokeMethod(page, "open", Q_ARG(QVariant, QString::fromLatin1(view)), Q_ARG(QVariant, arg));
            QTest::qWait(250);
        };
        auto back = [&] { QMetaObject::invokeMethod(page, "back"); QTest::qWait(100); };
        // People
        page->setProperty("tab", u"people"_s);
        QTest::qWait(250);
        QVERIFY(visibleByName(m_win, u"person-!28af67cc"_s));
        QVERIFY(!visibleByName(m_win, u"engineDown"_s));
        open("person", u"!28af67cc"_s);
        QVERIFY(visibleByName(m_win, u"assign-!28af67cc"_s));
        back();
        // Radios and the firmware row
        page->setProperty("tab", u"radios"_s);
        QTest::qWait(250);
        QVERIFY(visibleByName(m_win, u"radio-!28af67cc"_s));
        QVERIFY(visibleByName(m_win, u"firmwareRow"_s));
        QMetaObject::invokeMethod(visibleByName(m_win, u"assignToggle-!28af67cc"_s), "clicked");
        QTest::qWait(200);
        QVERIFY(visibleByName(m_win, u"assign-!28af67cc"_s));
        // Messages and a conversation: receive-only, so the compose box is off and says why; nothing is sent
        page->setProperty("tab", u"messages"_s);
        QTest::qWait(250);
        QVERIFY(visibleByName(m_win, u"thread-ch:meshtastic:0"_s));
        open("thread", u"ch:meshtastic:0"_s);
        auto *compose = visibleByName(m_win, u"composeField"_s);
        QVERIFY(compose);
        QVERIFY(!compose->isEnabled());
        QVERIFY(visibleByName(m_win, u"sendOff"_s));
        auto *sendBtn = visibleByName(m_win, u"sendButton"_s);
        QVERIFY(sendBtn && !sendBtn->isEnabled());
        QVERIFY(m_mesh->forbiddenSeen().isEmpty());
        QVERIFY(tak->property("unreadTotal").toInt() == 0);   // opening the thread read it
        back();
        // Settings: the fake engine does not read radio settings, so the screen says so and the write bar says why
        open("settings", u"!28af67cc"_s);
        QVERIFY(visibleByName(m_win, u"settingsUnavailable"_s));
        auto *review = visibleByName(m_win, u"reviewApply"_s);
        QVERIFY(review && !review->isEnabled());
        QVERIFY(visibleByName(m_win, u"applyReason"_s)->property("text").toString().contains(u"Will apply when a radio is connected and you confirm"_s));
        QCOMPARE(m_mesh->count(u"device.apply"_s), 0);
        back();
        // Firmware: library, then the guarded switch up to (not including) the flash
        open("firmware", QString());
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"fw-meshcore|heltec-v3"_s) != nullptr; }, 5000));
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"device-usb-10c4-ea60-SN1-1-1"_s) != nullptr; }, 5000));
        QMetaObject::invokeMethod(visibleByName(m_win, u"switch-usb-10c4-ea60-SN1-1-1"_s), "clicked");
        QTest::qWait(250);
        auto *choice = visibleByName(m_win, u"choice-meshtastic:heltec-v3:2.7.26.54e0d8d"_s);
        QVERIFY(choice);
        QMetaObject::invokeMethod(choice, "clicked");
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"confirmField"_s) != nullptr; }, 5000));
        auto *typed = visibleByName(m_win, u"confirmField"_s);
        auto *flash = visibleByName(m_win, u"flashButton"_s);
        QVERIFY(flash && !flash->isEnabled());
        typed->setProperty("text", u"heltec v3"_s);
        QTest::qWait(100);
        QVERIFY(!flash->isEnabled());                       // the name must be typed exactly
        typed->setProperty("text", u"Heltec V3"_s);
        QTest::qWait(100);
        QVERIFY(flash->isEnabled());
        QCOMPARE(m_fw->count(u"POST"_s, u"/v1/flash/run"_s), 0);   // nothing flashes until the person presses it
        QMetaObject::invokeMethod(page, "back");
        QTest::qWait(100);
        QVERIFY(g_warnings.isEmpty());
        // The engine goes away: the page says so instead of showing stale hope
        page->setProperty("tab", u"people"_s);
        m_mesh->close();
        QVERIFY(waitFor([&] { return !tak->property("available").toBool(); }, 8000));
        QTest::qWait(200);
        QVERIFY(visibleByName(m_win, u"engineDown"_s));
        QVERIFY(!m_win->grabWindow().isNull());
    }
    void real_pointer_wheel_and_keys_drive_the_map()
    {
        section("map");
        QVERIFY(waitFor([&] { return m_backend->map()->property("loaded").toBool(); }));
        QTest::qWait(300);
        auto *view = byName(m_win, u"mapView"_s);
        auto *stage = byName(m_win, u"mapStage"_s);
        QVERIFY(view && stage);
        QVERIFY(stage->activeFocusOnTab());
        const QPointF c = view->mapToScene(QPointF(view->width() / 2, view->height() / 2));
        const QPoint p0(int(c.x()), int(c.y()));
        // drag with the left button: the centre moves
        const double lon0 = view->property("centerLon").toDouble();
        QTest::mousePress(m_win, Qt::LeftButton, Qt::NoModifier, p0);
        for (int i = 1; i <= 8; ++i)
            QTest::mouseMove(m_win, p0 - QPoint(i * 12, 0));
        QTest::mouseRelease(m_win, Qt::LeftButton, Qt::NoModifier, p0 - QPoint(96, 0));
        QTest::qWait(50);
        QVERIFY(view->property("centerLon").toDouble() != lon0);
        // wheel zooms in
        const double z0 = view->property("zoom").toDouble();
        QWheelEvent wheel(QPointF(p0), m_win->mapToGlobal(QPointF(p0)), QPoint(), QPoint(0, 240), Qt::NoButton, Qt::NoModifier, Qt::NoScrollPhase, false);
        QCoreApplication::sendEvent(m_win, &wheel);
        QTest::qWait(50);
        QVERIFY(view->property("zoom").toDouble() > z0);
        // keys: the stage takes focus, + zooms, an arrow pans
        stage->forceActiveFocus();
        const double z1 = view->property("zoom").toDouble(), lat1 = view->property("centerLat").toDouble();
        QTest::keyClick(m_win, Qt::Key_Plus);
        QTest::keyClick(m_win, Qt::Key_Up);
        QTest::qWait(50);
        QVERIFY(view->property("zoom").toDouble() > z1);
        QVERIFY(view->property("centerLat").toDouble() != lat1);
        // a tap on empty map offers a marker; its button really adds one
        QTest::mouseClick(m_win, Qt::LeftButton, Qt::NoModifier, p0 + QPoint(40, 40));
        QTest::qWait(100);
        auto *drop = findItem(m_win->contentItem(), [](QQuickItem *i) { return i->isVisible() && i->property("text").toString() == u"Drop a marker here"_s; });
        QVERIFY(drop);
        const int before = m_backend->map()->property("markers").toList().size();
        QMetaObject::invokeMethod(drop, "clicked");
        QCOMPARE(m_backend->map()->property("markers").toList().size(), before + 1);
        const QVariantList ms = m_backend->map()->property("markers").toList();
        QMetaObject::invokeMethod(m_backend->map(), "removeMarker", Q_ARG(QString, ms.last().toMap().value(u"id"_s).toString()));
    }
    void pack_cards_survive_a_refresh()
    {
        section("library");
        auto *page = byName(m_win, u"page-library"_s);
        page->setProperty("mode", u"packs"_s);
        QTest::qWait(200);
        auto isCard = [](QQuickItem *i) { return i->objectName() == u"pack-local-lib"_s; };
        QQuickItem *card = findItem(m_win->contentItem(), isCard);
        QVERIFY(card);
        QPointer<QQuickItem> guard(card);
        for (int i = 0; i < 3; ++i) {
            QMetaObject::invokeMethod(m_backend->packs(), "rescan");
            QTest::qWait(450);
        }
        QVERIFY(guard);
        QCOMPARE(findItem(m_win->contentItem(), isCard), card);
        page->setProperty("mode", u"read"_s);
    }
    void zim_pictures_come_through_the_image_provider()
    {
        QMetaObject::invokeMethod(m_backend->library(), "open", Q_ARG(QString, u"local-lib"_s));
        ZimImageProvider provider;
        QSize size;
        const QImage img = provider.requestImage(u"local-lib/I/drop.png"_s, &size, QSize());
        QVERIFY(!img.isNull());
        QCOMPARE(size, QSize(1, 1));
        QVERIFY(provider.requestImage(u"local-lib/I/missing.png"_s, &size, QSize()).isNull());
        QVERIFY(provider.requestImage(u"local-lib/index"_s, &size, QSize()).isNull());   // not an image
        QVERIFY(provider.requestImage(u"other-pack/I/drop.png"_s, &size, QSize()).isNull());
    }
    void kit_lists_the_templates()
    {
        section("kit");
        QVERIFY(visibleByName(m_win, u"page-kit"_s));
        QVERIFY(waitFor([&] { return visibleByName(m_win, u"kit-pocket"_s) != nullptr; }));
        QVERIFY(visibleByName(m_win, u"kit-laptop"_s));
        QVERIFY(!m_win->grabWindow().isNull());
    }
    void keyboard_switches_sections()
    {
        section("library");
        QTest::keyClick(m_win, Qt::Key_2, Qt::ControlModifier);
        QTest::qWait(100);
        QCOMPARE(m_win->property("section").toString(), u"map"_s);
        QTest::keyClick(m_win, Qt::Key_4, Qt::ControlModifier);
        QTest::qWait(100);
        QCOMPARE(m_win->property("section").toString(), u"kit"_s);
        QTest::keyClick(m_win, Qt::Key_1, Qt::ControlModifier);
        QTest::qWait(100);
        QCOMPARE(m_win->property("section").toString(), u"library"_s);
    }
    void phone_layout_has_a_tab_bar_and_no_sidebar()
    {
        QVERIFY(!visibleByName(m_win, u"phoneTabs"_s));
        QVERIFY(visibleByName(m_win, u"section-library"_s));
        m_win->resize(412, 915);
        QVERIFY(waitFor([&] { return m_win->property("narrow").toBool(); }));
        QTest::qWait(250);
        QVERIFY(visibleByName(m_win, u"phoneTabs"_s));
        QVERIFY(!visibleByName(m_win, u"section-library"_s));
        for (const char *s : {"library", "map", "taostak", "kit", "agent"}) {
            section(s);
            QVERIFY(!m_win->grabWindow().isNull());
        }
        m_win->resize(1500, 950);
        QVERIFY(waitFor([&] { return !m_win->property("narrow").toBool(); }));
    }
    void agent_panel_pops_out_and_docks_back()
    {
        section("library");
        QTest::qWait(200);
        QVERIFY(!m_win->property("agentPopped").toBool());
        m_win->setProperty("agentPopped", true);
        QTest::qWait(300);
        auto *pop = m_win->findChild<QQuickWindow *>(u"agentWindow"_s);
        QVERIFY(pop);
        QVERIFY(pop->isVisible());
        m_win->setProperty("agentPopped", false);
        QTest::qWait(200);
        QVERIFY(!pop->isVisible());
    }
    void no_qml_warnings_in_any_section()
    {
        QVERIFY2(g_warnings.isEmpty(), qPrintable(g_warnings.join(u"\n"_s)));
    }
};

QTEST_MAIN(TstSurvivalUi)
#include "tst_survival_ui.moc"
