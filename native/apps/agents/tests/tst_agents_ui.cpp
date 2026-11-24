// SPDX-License-Identifier: AGPL-3.0-or-later
// The Agents window loaded in a QML engine against the fake controller: the
// list, selection, the detail, the stop confirmation and the exact request, a
// refusal next to the controls, the empty state and the forbidden state.
#include "apphost.h"

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
void click(QQuickItem *b) { QMetaObject::invokeMethod(b, "clicked"); }
QQuickItem *rowFor(QQuickWindow *w, const QString &agent)
{
    return findItem(w->contentItem(), [&](QQuickItem *i) { return i->property("agentName").toString() == agent && i->isVisible(); });
}
} // namespace

class TstAgentsUi : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    QQuickWindow *m_win = nullptr;

    void open(const QString &scenario = u"seed"_s, int listFail = 0)
    {
        delete m_host;
        m_host = nullptr;
        m_fake->config({{u"agents"_s, scenario}, {u"admin"_s, false}, {u"list_fail"_s, listFail}});
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.agents"_s);
        QVERIFY(m_host->waitSignedIn());
        m_win = m_host->load(u"qrc:/my.taos.agents/Main.qml"_s);
        QVERIFY(m_win);
    }
    int listCount() { auto *l = byName(m_win, u"agentList"_s); return l ? l->property("count").toInt() : -1; }

private Q_SLOTS:
    void initTestCase()
    {
        m_fake = new FakeHost;
        QVERIFY(m_fake->ok());
    }
    void cleanupTestCase()
    {
        delete m_host;
        delete m_fake;
    }

    void list_shows_every_agent_with_a_state_word_and_a_device()
    {
        open();
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QVERIFY(!visibleByName(m_win, u"skeleton"_s));
        QVERIFY(waitFor([this] { return rowFor(m_win, u"scribe"_s) && rowFor(m_win, u"builder"_s); }));
        auto *r = rowFor(m_win, u"scribe"_s);
        // the state is a word and the device a name with its reachability, not colour alone
        QCOMPARE(r->property("agentState").toString(), u"paused"_s);
        QCOMPARE(r->property("deviceName").toString(), u"pixel"_s);
        QCOMPARE(r->property("deviceKind").toString(), u"phone"_s);
        QCOMPARE(r->property("deviceReach").toString(), u"online"_s);
        auto *off = rowFor(m_win, u"builder"_s);
        QCOMPARE(off->property("deviceReach").toString(), u"offline"_s);
        QCOMPARE(rowFor(m_win, u"researcher"_s)->property("deviceName").toString(), u"This device"_s);
        QVERIFY(byName(m_win, u"taosAgentRow"_s)->isVisible());
    }

    void detail_shows_overview_and_the_device()
    {
        open();
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QVERIFY(waitFor([this] { return rowFor(m_win, u"scribe"_s) != nullptr; }));
        click(rowFor(m_win, u"scribe"_s));
        auto *facts = byName(m_win, u"facts"_s);
        QVERIFY(facts);
        QVERIFY(waitFor([&] {
            const QVariantList items = facts->property("items").toList();
            for (const QVariant &v : items)
                if (v.toMap().value(u"label"_s).toString() == u"Device"_s)
                    return v.toMap().value(u"value"_s).toString().contains(u"pixel"_s);
            return false;
        }));
        QCOMPARE(byName(m_win, u"detailState"_s)->property("text").toString(), u"Paused"_s);
        QVERIFY(visibleByName(m_win, u"action_resume"_s));
        QVERIFY(!visibleByName(m_win, u"action_stop"_s));
    }

    void stop_asks_first_then_sends_one_request_and_the_list_updates()
    {
        open();
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QVERIFY(waitFor([this] { return rowFor(m_win, u"researcher"_s) != nullptr; }));
        click(rowFor(m_win, u"researcher"_s));
        auto *stop = visibleByName(m_win, u"action_stop"_s);
        QVERIFY(stop);
        click(stop);
        QVERIFY(visibleByName(m_win, u"confirmBar"_s));
        QCOMPARE(m_fake->calls().size(), 0); // nothing sent before the answer
        click(byName(m_win, u"confirmNo"_s));
        QVERIFY(!visibleByName(m_win, u"confirmBar"_s));
        click(visibleByName(m_win, u"action_stop"_s));
        click(byName(m_win, u"confirmYes"_s));
        QVERIFY(waitFor([this] { return m_fake->calls().size() == 1; }));
        QCOMPARE(m_fake->calls().first().toObject().value(u"path"_s).toString(), u"/api/agents/researcher/stop"_s);
        QVERIFY(waitFor([this] { return byName(m_win, u"detailState"_s)->property("text").toString() == u"Stopped"_s; }));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"action_start"_s) != nullptr; }));
    }

    void a_refusal_appears_next_to_the_controls()
    {
        open();
        m_fake->config({{u"outcome"_s, QJsonObject{{u"courier/restart"_s, QJsonObject{{u"status"_s, 403}, {u"body"_s, QJsonObject{{u"detail"_s, u"forbidden"_s}}}}}}}});
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QVERIFY(waitFor([this] { return rowFor(m_win, u"courier"_s) != nullptr; }));
        click(rowFor(m_win, u"courier"_s));
        click(visibleByName(m_win, u"action_restart"_s));
        click(byName(m_win, u"confirmYes"_s));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"failureBar"_s) != nullptr; }));
        auto *dir = m_win->findChild<QObject *>(u"directory"_s);
        QString text;
        QMetaObject::invokeMethod(dir, "actionError", Q_RETURN_ARG(QString, text), Q_ARG(QString, u"courier"_s));
        QVERIFY2(text.contains(u"not allowed"_s), qPrintable(text));
        QCOMPARE(byName(m_win, u"detailState"_s)->property("text").toString(), u"Running"_s);
    }

    void device_filter_and_grouping_are_in_the_window()
    {
        open();
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QVERIFY(visibleByName(m_win, u"deviceFilter"_s));
        click(visibleByName(m_win, u"groupToggle"_s));
        QCOMPARE(listCount(), 5);
        auto *dir = m_win->findChild<QObject *>(u"directory"_s);
        QVERIFY(dir);
        QVERIFY(dir->property("groupByDevice").toBool());
        dir->setProperty("deviceFilter", u"pixel"_s);
        QVERIFY(waitFor([this] { return listCount() == 2; }));
    }

    void empty_install_explains_how_to_create_an_agent()
    {
        open(u"empty"_s);
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"emptyState"_s) != nullptr; }));
        QVERIFY(visibleByName(m_win, u"openWizard"_s));
        QVERIFY(visibleByName(m_win, u"taosAgentRow"_s)); // the built-in agent is still there
    }

    void a_403_on_the_list_shows_the_forbidden_state()
    {
        open(u"seed"_s, 403);
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"errorState"_s) != nullptr; }));
        QVERIFY(!visibleByName(m_win, u"agentList"_s));
    }

    void the_agent_argument_opens_that_agent()
    {
        open();
        QVERIFY(waitFor([this] { return listCount() == 5; }));
        QMetaObject::invokeMethod(m_win, "open", Q_ARG(QVariant, QVariant(u"courier"_s)));
        QVERIFY(waitFor([this] { return m_win->property("selected").toString() == u"courier"_s; }));
        QMetaObject::invokeMethod(m_win, "open", Q_ARG(QVariant, QVariant(u"nobody"_s)));
        QVERIFY(waitFor([this] { return visibleByName(m_win, u"toast"_s) != nullptr; }));
        QCOMPARE(m_win->property("selected").toString(), u"courier"_s);
    }
};

QTEST_MAIN(TstAgentsUi)
#include "tst_agents_ui.moc"
