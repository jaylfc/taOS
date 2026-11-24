// SPDX-License-Identifier: AGPL-3.0-or-later
// The Agents engine against the fake controller: list order, grouping and
// filters, live state, the device of every agent, lifecycle in-progress and
// failure states (403, 409, 500, a stop that did not stop).
#include "agentdirectory.h"
#include "apphost.h"

using namespace Qt::StringLiterals;
using namespace Taos::Apps;

namespace {
QStringList titles(QAbstractItemModel *m)
{
    QStringList out;
    const int role = [&] {
        const auto n = m->roleNames();
        for (auto it = n.cbegin(); it != n.cend(); ++it)
            if (it.value() == "title")
                return it.key();
        return -1;
    }();
    for (int i = 0; i < m->rowCount(); ++i)
        out << m->data(m->index(i, 0), role).toString();
    return out;
}
} // namespace

class TstAgentsLogic : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    AgentDirectory *m_dir = nullptr;

    void start(bool admin = false)
    {
        delete m_dir;
        m_dir = nullptr;
        delete m_host;
        m_host = nullptr;
        m_fake->config({{u"agents"_s, u"seed"_s}, {u"admin"_s, admin}, {u"outcome"_s, QJsonObject()},
                        {u"agent_delay_ms"_s, 0}, {u"list_fail"_s, 0}});
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.agents"_s);
        QVERIFY(m_host->waitSignedIn());
        m_dir = new AgentDirectory;
        m_dir->setSession(&m_host->session());
        m_dir->setRunning(true);
    }
    void loaded() { QVERIFY(waitFor([this] { return m_dir->loaded(); })); }
    QVariantMap row(const QString &n) { return m_dir->agent(n); }

private Q_SLOTS:
    void initTestCase()
    {
        m_fake = new FakeHost;
        QVERIFY(m_fake->ok());
    }
    void cleanupTestCase()
    {
        delete m_dir;
        delete m_host;
        delete m_fake;
    }
    void cleanup()
    {
        delete m_dir;
        m_dir = nullptr;
    }

    void list_is_sorted_by_name_and_states_come_from_containers()
    {
        start();
        loaded();
        QCOMPARE(m_dir->totalCount(), 5);
        QCOMPARE(titles(m_dir->model()), (QStringList{u"Analyst"_s, u"Builder"_s, u"Courier"_s, u"Researcher"_s, u"Scribe"_s}));
        QCOMPARE(row(u"researcher"_s).value(u"state"_s).toString(), u"running"_s);
        QCOMPARE(row(u"scribe"_s).value(u"state"_s).toString(), u"paused"_s);   // Frozen container
        QCOMPARE(row(u"builder"_s).value(u"state"_s).toString(), u"stopped"_s);
        QCOMPARE(row(u"analyst"_s).value(u"state"_s).toString(), u"error"_s);
        const QVariantMap c = m_dir->stateCounts();
        QCOMPARE(c.value(u"running"_s).toInt(), 2);
        QCOMPARE(c.value(u"paused"_s).toInt(), 1);
        QVERIFY(!row(u"researcher"_s).contains(u"llm_key"_s));
        QCOMPARE(row(u"researcher"_s).value(u"actions"_s).toStringList(), (QStringList{u"pause"_s, u"restart"_s, u"stop"_s}));
        QCOMPARE(row(u"scribe"_s).value(u"actions"_s).toStringList(), (QStringList{u"resume"_s}));
    }

    void every_agent_names_its_device()
    {
        start();
        loaded();
        const QVariantMap here = row(u"researcher"_s).value(u"device"_s).toMap();
        QVERIFY(here.value(u"local"_s).toBool());
        const QVariantMap phone = row(u"scribe"_s).value(u"device"_s).toMap();
        QCOMPARE(phone.value(u"name"_s).toString(), u"pixel"_s);
        QCOMPARE(phone.value(u"kind"_s).toString(), u"phone"_s);
        QCOMPARE(phone.value(u"reach"_s).toString(), u"online"_s);
        QCOMPARE(row(u"builder"_s).value(u"device"_s).toMap().value(u"reach"_s).toString(), u"offline"_s);
        const QVariantList d = m_dir->devices();
        QCOMPARE(d.size(), 3);
        QCOMPARE(d.at(0).toMap().value(u"value"_s).toString(), u"@local"_s); // this device first
        QCOMPARE(d.at(0).toMap().value(u"count"_s).toInt(), 2);
    }

    void grouping_puts_this_device_first()
    {
        start();
        loaded();
        m_dir->setGroupByDevice(true);
        QCOMPARE(titles(m_dir->model()), (QStringList{u"Analyst"_s, u"Researcher"_s, u"Builder"_s, u"Courier"_s, u"Scribe"_s}));
        m_dir->setGroupByDevice(false);
        QCOMPARE(titles(m_dir->model()).first(), u"Analyst"_s);
    }

    void filters_combine()
    {
        start();
        loaded();
        m_dir->setFilterText(u"PIXEL"_s); // matches the device name, case-insensitively
        QCOMPARE(titles(m_dir->model()), (QStringList{u"Courier"_s, u"Scribe"_s}));
        m_dir->setStateFilter(u"running"_s);
        QCOMPARE(titles(m_dir->model()), (QStringList{u"Courier"_s}));
        m_dir->setFilterText({});
        m_dir->setStateFilter({});
        m_dir->setDeviceFilter(u"ghost-box"_s);
        QCOMPARE(titles(m_dir->model()), (QStringList{u"Builder"_s}));
        m_dir->setDeviceFilter(u"@local"_s);
        QCOMPARE(m_dir->shownCount(), 2);
        m_dir->setDeviceFilter({});
        m_dir->setFilterText(u"hermes"_s); // framework
        QCOMPARE(m_dir->shownCount(), 2);
        m_dir->setFilterText(u"nothing like this"_s);
        QCOMPARE(m_dir->shownCount(), 0);
        QCOMPARE(m_dir->totalCount(), 5);
    }

    void owner_is_joined_for_admins_only()
    {
        start(false);
        loaded();
        QVERIFY(!m_dir->isAdmin());
        QCOMPARE(row(u"scribe"_s).value(u"owner_id"_s).toString(), QString());
        start(true);
        loaded();
        QVERIFY(m_dir->isAdmin());
        QVERIFY(waitFor([this] { return row(u"scribe"_s).value(u"owner_id"_s).toString() == u"u-amy"_s; }));
    }

    void stop_shows_progress_then_the_new_state_and_ignores_a_second_press()
    {
        start();
        loaded();
        m_fake->config({{u"agent_delay_ms"_s, 400}});
        QSignalSpy done(m_dir, &AgentDirectory::actionFinished);
        m_dir->act(u"researcher"_s, u"stop"_s);
        QVERIFY(m_dir->isBusy(u"researcher"_s));
        QCOMPARE(m_dir->busyAction(u"researcher"_s), u"stop"_s);
        m_dir->act(u"researcher"_s, u"stop"_s); // ignored
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QCOMPARE(m_fake->calls().size(), 1);
        QVERIFY(done.first().at(2).toBool());
        QVERIFY(!m_dir->isBusy(u"researcher"_s));
        QVERIFY(waitFor([this] { return row(u"researcher"_s).value(u"state"_s).toString() == u"stopped"_s; }));
        QCOMPARE(row(u"researcher"_s).value(u"actions"_s).toStringList(), (QStringList{u"start"_s}));
    }

    void transitions_pause_resume_start()
    {
        start();
        loaded();
        QSignalSpy done(m_dir, &AgentDirectory::actionFinished);
        m_dir->act(u"courier"_s, u"pause"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(waitFor([this] { return row(u"courier"_s).value(u"state"_s).toString() == u"paused"_s; }));
        m_dir->act(u"courier"_s, u"resume"_s);
        QVERIFY(waitFor([&] { return done.count() == 2; }));
        QVERIFY(waitFor([this] { return row(u"courier"_s).value(u"state"_s).toString() == u"running"_s; }));
        m_dir->act(u"builder"_s, u"start"_s);
        QVERIFY(waitFor([&] { return done.count() == 3; }));
        QVERIFY(waitFor([this] { return row(u"builder"_s).value(u"state"_s).toString() == u"running"_s; }));
    }

    void a_refusal_is_shown_in_words_and_changes_nothing()
    {
        start();
        loaded();
        m_fake->config({{u"outcome"_s, QJsonObject{{u"builder/start"_s, QJsonObject{{u"status"_s, 403}, {u"body"_s, QJsonObject{{u"detail"_s, u"forbidden"_s}}}}}}}});
        QSignalSpy done(m_dir, &AgentDirectory::actionFinished);
        m_dir->act(u"builder"_s, u"start"_s);
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(!done.first().at(2).toBool());
        const QString text = done.first().at(3).toString();
        QVERIFY2(text.contains(u"not allowed"_s) && text.contains(u"forbidden"_s), qPrintable(text));
        QCOMPARE(m_dir->actionError(u"builder"_s), text);
        QCOMPARE(row(u"builder"_s).value(u"state"_s).toString(), u"stopped"_s);
        m_dir->clearActionError(u"builder"_s);
        QVERIFY(m_dir->actionError(u"builder"_s).isEmpty());
    }

    void conflict_server_error_and_a_stop_that_did_not_stop()
    {
        start();
        loaded();
        QSignalSpy done(m_dir, &AgentDirectory::actionFinished);
        m_dir->act(u"builder"_s, u"pause"_s); // not running: 409 with the controller's sentence
        QVERIFY(waitFor([&] { return done.count() == 1; }));
        QVERIFY(done.at(0).at(3).toString().contains(u"is not running"_s));
        m_fake->config({{u"outcome"_s, QJsonObject{
            {u"analyst/restart"_s, QJsonObject{{u"status"_s, 500}, {u"body"_s, QJsonObject{{u"error"_s, u"Could not restart agent 'analyst': no space"_s}}}}},
            {u"researcher/stop"_s, QJsonObject{{u"status"_s, 200}, {u"body"_s, QJsonObject{
                {u"stop_result"_s, QJsonObject{{u"success"_s, false}, {u"output"_s, u"device busy"_s}}}, {u"force_killed"_s, false}}}}}}}});
        m_dir->act(u"analyst"_s, u"restart"_s);
        QVERIFY(waitFor([&] { return done.count() == 2; }));
        QVERIFY(done.at(1).at(3).toString().contains(u"no space"_s));
        m_dir->act(u"researcher"_s, u"stop"_s);
        QVERIFY(waitFor([&] { return done.count() == 3; }));
        QVERIFY(!done.at(2).at(2).toBool());
        QCOMPARE(done.at(2).at(3).toString(), u"device busy"_s);
        QCOMPARE(row(u"researcher"_s).value(u"state"_s).toString(), u"running"_s);
    }

    void a_403_on_the_list_is_a_forbidden_state_not_a_crash()
    {
        m_fake->config({{u"agents"_s, u"seed"_s}});
        m_fake->config({{u"list_fail"_s, 403}});
        delete m_host;
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.agents"_s);
        QVERIFY(m_host->waitSignedIn());
        m_dir = new AgentDirectory;
        m_dir->setSession(&m_host->session());
        m_dir->setRunning(true);
        QVERIFY(waitFor([this] { return m_dir->errorKind() == u"forbidden"_s; }));
        QVERIFY(!m_dir->loaded());
        QCOMPARE(m_dir->errorText(), u"forbidden"_s);
        // access comes back: the list loads and the error clears
        m_fake->config({{u"list_fail"_s, 0}});
        m_dir->refresh();
        loaded();
        QVERIFY(m_dir->errorKind().isEmpty());
    }

    void empty_install_is_loaded_and_empty()
    {
        m_fake->config({{u"agents"_s, u"empty"_s}});
        delete m_host;
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.agents"_s);
        QVERIFY(m_host->waitSignedIn());
        m_dir = new AgentDirectory;
        m_dir->setSession(&m_host->session());
        m_dir->setRunning(true);
        loaded();
        QCOMPARE(m_dir->totalCount(), 0);
        QVERIFY(waitFor([this] { return !m_dir->taosAgent().isEmpty(); }));
        QCOMPARE(m_dir->taosAgent().value(u"model"_s).toString(), u"qwen3-8b"_s);
    }

    void activity_loads_for_one_agent()
    {
        start();
        loaded();
        m_dir->loadActivity(u"researcher"_s);
        QVERIFY(waitFor([this] { return m_dir->activityState() == u"ready"_s; }));
        QCOMPARE(m_dir->activity().size(), 2);
        QCOMPARE(m_dir->activity().first().toMap().value(u"kind"_s).toString(), u"chat.reply"_s);
    }

    void message_button_fails_politely_without_the_messages_app()
    {
        start();
        if (m_dir->canMessage())
            QSKIP("taos-messages is installed here");
        QSignalSpy failed(m_dir, &AgentDirectory::launchFailed);
        QVERIFY(!m_dir->messageAgent(u"researcher"_s));
        QCOMPARE(failed.count(), 1);
    }
};

QTEST_MAIN(TstAgentsLogic)
#include "tst_agents_logic.moc"
