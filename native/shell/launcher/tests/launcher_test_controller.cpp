#include <QClipboard>
#include <QGuiApplication>
#include <QtTest>

#include "controller.h"
#include "providers_more.h"

using namespace taos::search;
using taos::launcher::Controller;

namespace {
// One result with a confirm step whose action runs `true`: never a real power command in a test.
class ConfirmProvider : public Provider {
public:
    QString id() const override { return "confirm"; }
    QString name() const override { return "confirm"; }
    QList<Result> query(const QString &text, const CancelToken &) override {
        if (!text.startsWith("danger")) return {};
        Result r; r.id = "cmd:danger"; r.kind = "command"; r.title = "Danger"; r.score = 1.0;
        Action a; a.id = "run"; a.label = "Confirm"; a.verb = "exec"; a.args = { "true" }; a.confirm = true;
        r.actions << a;
        return { r };
    }
};
}  // namespace

class TestController : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    SearchEngine m_engine;
    std::shared_ptr<FakeSender> m_sender = std::make_shared<FakeSender>();
    std::shared_ptr<LauncherConfig> m_cfg;
    std::shared_ptr<Frecency> m_frec;
    std::unique_ptr<Controller> m_c;

    void waitFinal() {
        QTest::qWait(60);
        QTRY_VERIFY_WITH_TIMEOUT(!m_c->searching(), 2000);
    }
    void type(const QString &q) { m_c->setQuery(q); waitFinal(); }
    QString rowTitle(int i) { return m_c->model().data(m_c->model().index(i), taos::launcher::RowModel::TitleRole).toString(); }
private slots:
    void initTestCase() {
        qRegisterMetaType<QList<taos::search::Row>>();
        m_frec = std::make_shared<Frecency>(m_tmp.path() + "/f.json");
        m_cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/c.json");
        auto dir = std::make_shared<FakeDirectory>(QList<Agent>{ { "a1", "Parker", "online" }, { "a2", "Quill", "offline" } });
        auto apps = std::make_shared<AppsProvider>(m_frec, QStringList{ QStringLiteral(TAOS_LAUNCHER_FIXTURES) + "/applications" }, "C", QStringList{});
        m_engine.addProvider(apps);
        m_engine.addProvider(std::make_shared<CalcProvider>());
        m_engine.addProvider(std::make_shared<MentionProvider>(dir, m_cfg, false));
        m_engine.addProvider(std::make_shared<ConfirmProvider>());
        m_engine.addProvider(std::make_shared<AskProvider>(m_cfg, [] { return QList<DesktopEntry>(); }));
        m_c = std::make_unique<Controller>(&m_engine, m_frec);
        m_c->setConfig(m_cfg);
        m_c->setSender(m_sender);
        m_c->setLaunchDelay(0);
    }
    void init() {
        if (m_c->isOpen()) { m_c->hide(); m_c->closeFinished(); }
        m_sender->opened.clear(); m_sender->lastIds.clear(); m_sender->lastText.clear();
        m_cfg->setMentionOpensChat(false);
    }
    void toggleShowsAndHides() {
        QSignalSpy show(m_c.get(), &Controller::showWindow), hide(m_c.get(), &Controller::hideWindow);
        QCOMPARE(m_c->handleCommand({ "toggle" }), QString("ok shown"));
        QVERIFY(m_c->isOpen());
        QCOMPARE(show.size(), 1);
        QCOMPARE(m_c->handleCommand({ "toggle" }), QString("ok hidden"));
        QVERIFY(!m_c->isOpen());
        QCOMPARE(hide.size(), 0);   // the window goes only when the QML says its fade out is done
        m_c->closeFinished();
        QCOMPARE(hide.size(), 1);
        QVERIFY(m_c->handleCommand({ "ping" }).startsWith("ok"));
        QVERIFY(m_c->handleCommand({ "nonsense" }).startsWith("error"));
    }
    void reopenDuringFadeKeepsWindow() {
        QSignalSpy hide(m_c.get(), &Controller::hideWindow);
        m_c->show(); m_c->hide(); m_c->show();
        m_c->closeFinished();
        QCOMPARE(hide.size(), 0);
        QVERIFY(m_c->isOpen());
    }
    void escapeClearsThenCloses() {
        m_c->show(); waitFinal();
        type("fire");
        QVERIFY(m_c->count() > 0);
        m_c->escape();
        QVERIFY(m_c->isOpen());
        QCOMPARE(m_c->query(), QString());
        m_c->escape();
        QVERIFY(!m_c->isOpen());
    }
    void selectionMovesAndWraps() {
        m_c->show(); waitFinal();
        type("t");
        QVERIFY(m_c->count() >= 3);
        QCOMPARE(m_c->currentIndex(), 0);
        m_c->move(-1);
        QCOMPARE(m_c->currentIndex(), m_c->count() - 1);
        m_c->move(1);
        QCOMPARE(m_c->currentIndex(), 0);
    }
    void calculatorCopies() {
        m_c->show(); waitFinal();
        type("6*7");
        QCOMPARE(rowTitle(0), QString("42"));
        QCOMPARE(m_c->currentHint(), QString("Copy"));
        m_c->activate();
        QCOMPARE(QGuiApplication::clipboard()->text(), QString("42"));
        QVERIFY(m_c->launching());
    }
    void askRowIsLastAndStubsHonestly() {
        m_c->show(); waitFinal();
        type("what is love");
        QCOMPARE(rowTitle(m_c->count() - 1), QString("Ask taOS: what is love"));
        m_c->move(-1);   // select the Ask row
        QCOMPARE(m_c->currentKind(), QString("ask"));
        m_c->activate();
        QVERIFY(m_c->isOpen());   // a stub does not close the launcher
        QCOMPARE(m_c->notice(), QString("Agent chat is not set up yet"));
    }
    void confirmStepNeedsSecondEnter() {
        m_c->show(); waitFinal();
        type("danger");
        QCOMPARE(m_c->currentTitle(), QString("Danger"));
        m_c->activate();
        QCOMPARE(m_c->currentHint(), QString("Press Enter to confirm"));
        QVERIFY(!m_c->launching());
        m_c->escape();                      // backs out of the confirm, does not close
        QVERIFY(m_c->isOpen());
        QCOMPARE(m_c->currentHint(), QString("Confirm"));
        m_c->activate(); m_c->activate();   // now confirm
        QVERIFY(m_c->launching());
    }
    void tabShowsActions() {
        m_c->show(); waitFinal();
        type("fire");
        QCOMPARE(m_c->currentTitle(), QString("Firefox"));
        m_c->openActions();
        QVERIFY(m_c->actionsOpen());
        QVERIFY(m_c->actionLabels().contains("New Window"));
        m_c->moveAction(1);
        QCOMPARE(m_c->actionIndex(), 1);
        m_c->closeActions();
        QVERIFY(!m_c->actionsOpen());
    }
    void mentionAutocompleteByTab() {
        m_c->show(); waitFinal();
        QSignalSpy set(m_c.get(), &Controller::setInputText);
        type("@par");
        QCOMPARE(rowTitle(0), QString("@parker"));
        QCOMPARE(m_c->currentHint(), QString("Complete"));
        m_c->openActions();   // Tab on a mention completes it
        QCOMPARE(set.size(), 1);
        QCOMPARE(set.first().at(0).toString(), QString("@parker "));
        QVERIFY(!m_c->actionsOpen());
        QVERIFY(m_c->isOpen());
        // Enter completes too
        m_c->activate();
        QCOMPARE(set.size(), 2);
    }
    void mentionSendOnly() {
        m_c->show(); waitFinal();
        type("@parker how is the build");
        QCOMPARE(m_c->currentHint(), QString("Send"));
        m_c->activate();
        QCOMPARE(m_sender->lastIds, QStringList({ "a1" }));
        QCOMPARE(m_sender->lastText, QString("how is the build"));
        QVERIFY(m_sender->opened.isEmpty());
        QCOMPARE(m_c->notice(), QString("Sent to @parker"));
    }
    void mentionShiftEnterOpensChat() {
        m_c->show(); waitFinal();
        type("@parker how is the build");
        m_c->activateAlternate();
        QCOMPARE(m_sender->opened, QStringList({ "a1" }));
        QCOMPARE(m_c->notice(), QString("Sent to @parker"));
    }
    void mentionConfigFlipsDefault() {
        m_cfg->setMentionOpensChat(true);
        m_c->show();   // show() reloads the config file (absent = defaults), so set it again afterwards
        m_cfg->setMentionOpensChat(true);
        waitFinal();
        type("@parker hi");
        QCOMPARE(m_c->currentHint(), QString("Send and open"));
        m_c->activate();
        QCOMPARE(m_sender->opened, QStringList({ "a1" }));
    }
    void mentionOpenWithoutChatWindowSaysSo() {
        m_sender->canOpen = false;
        m_cfg->setMentionOpensChat(false);
        m_c->show(); waitFinal();
        type("@parker hi");
        m_c->activateAlternate();
        QVERIFY(m_c->notice().contains("reply will arrive as a notification"));
        m_sender->canOpen = true;
    }
    void mentionSendFailureStaysOpen() {
        m_sender->nextOk = false;
        m_c->show(); waitFinal();
        type("@parker hi");
        m_c->activate();
        QVERIFY(m_c->isOpen());
        QCOMPARE(m_c->notice(), QString("could not send"));
        m_sender->nextOk = true;
    }
};
QTEST_MAIN(TestController)
#include "launcher_test_controller.moc"
