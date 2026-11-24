#include <QtTest>
#include <thread>

#include "engine.h"

using namespace taos::search;

namespace {
// A provider with a fixed answer after a delay that respects cancellation.
class FakeProvider : public Provider {
public:
    FakeProvider(QString id, QString kind, int delayMs, int budgetMs, double score = 0.8) : m_id(id), m_kind(kind), m_delay(delayMs), m_budget(budgetMs), m_score(score) {}
    QString id() const override { return m_id; }
    QString name() const override { return m_id; }
    int timeBudgetMs() const override { return m_budget; }
    bool available() const override { return m_available; }
    QList<Result> query(const QString &text, const CancelToken &cancel) override {
        for (int i = 0; i < m_delay / 5; ++i) {
            if (cancel.cancelled()) return {};
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        m_calls++;
        Result r;
        r.id = m_kind + ":" + text;
        r.provider = m_id;
        r.kind = m_kind;
        r.title = m_id + " " + text;
        r.score = m_score;
        r.actions << Action{"go", "Go", "exec", {}, {}};
        return { r };
    }
    QString m_id, m_kind;
    int m_delay, m_budget;
    double m_score;
    bool m_available = true;
    std::atomic<int> m_calls{0};
};
}  // namespace

class TestEngine : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() { qRegisterMetaType<QList<taos::search::Row>>(); }

    void slowProviderNeverBlocksTheFastOne() {
        SearchEngine e;
        auto fast = std::make_shared<FakeProvider>("fast", "app", 0, 200);
        auto slow = std::make_shared<FakeProvider>("slow", "window", 2000, 120);
        e.addProvider(fast);
        e.addProvider(slow);
        QSignalSpy upd(&e, &SearchEngine::updated);
        QSignalSpy to(&e, &SearchEngine::providerTimedOut);
        QElapsedTimer t; t.start();
        e.search("x");
        QTRY_VERIFY_WITH_TIMEOUT(!upd.isEmpty(), 1000);
        QVERIFY2(t.elapsed() < 100, "the fast provider's rows must arrive at once");
        QCOMPARE(upd.first().at(1).value<QList<Row>>().size(), 1);
        QVERIFY(!upd.first().at(2).toBool());   // not final: slow is pending
        QTRY_COMPARE_WITH_TIMEOUT(to.size(), 1, 1000);
        QCOMPARE(to.first().at(0).toString(), QString("slow"));
        QVERIFY(upd.last().at(2).toBool());      // final once the slow one is written off
        QVERIFY2(t.elapsed() < 700, "final must come at the slow provider's time box, not its full delay");
        QCOMPARE(upd.last().at(1).value<QList<Row>>().size(), 1);   // the slow provider contributed nothing
    }
    void lateResultsAreDropped() {
        SearchEngine e;
        // finishes at 150 ms, after its 60 ms box
        auto late = std::make_shared<FakeProvider>("late", "app", 150, 60);
        e.addProvider(late);
        QSignalSpy upd(&e, &SearchEngine::updated);
        e.search("q");
        QTRY_VERIFY_WITH_TIMEOUT(!upd.isEmpty(), 1000);
        QTest::qWait(300);
        for (const auto &args : upd) QCOMPARE(args.at(1).value<QList<Row>>().size(), 0);
    }
    void staleGenerationIsIgnoredAndCancelled() {
        SearchEngine e;
        auto p = std::make_shared<FakeProvider>("p", "app", 120, 500);
        e.addProvider(p);
        QSignalSpy upd(&e, &SearchEngine::updated);
        const int g1 = e.search("a");
        const int g2 = e.search("ab");
        QVERIFY(g2 > g1);
        QTRY_VERIFY_WITH_TIMEOUT(!upd.isEmpty(), 1500);
        QTest::qWait(200);
        for (const auto &args : upd) QCOMPARE(args.at(0).toInt(), g2);
        QCOMPARE(upd.last().at(1).value<QList<Row>>().first().r.id, QString("app:ab"));
    }
    void unavailableProviderIsSkipped() {
        SearchEngine e;
        auto p = std::make_shared<FakeProvider>("p", "app", 0, 100);
        p->m_available = false;
        e.addProvider(p);
        QSignalSpy upd(&e, &SearchEngine::updated);
        e.search("a");
        QTRY_VERIFY_WITH_TIMEOUT(!upd.isEmpty(), 500);
        QVERIFY(upd.last().at(2).toBool());
        QCOMPARE(p->m_calls.load(), 0);
    }
    void arrangeTopHitAndGroups() {
        QList<Result> in;
        const auto mk = [](QString id, QString kind, double s) { Result r; r.id = id; r.kind = kind; r.title = id; r.score = s; return r; };
        in << mk("f1", "file", 0.9) << mk("a1", "app", 0.9) << mk("a2", "app", 0.5) << mk("w1", "window", 0.6) << mk("c1", "command", 0.5) << mk("ask", "ask", 0.01);
        const QList<Row> rows = arrange(in, "x");
        QCOMPARE(rows.first().section, QString("Top hit"));
        QCOMPARE(rows.first().r.id, QString("a1"));
        QStringList sections;
        for (const Row &r : rows) if (!sections.contains(r.section)) sections << r.section;
        QCOMPARE(sections, QStringList({ "Top hit", "Apps", "Windows", "Actions", "Files", "Ask" }));
        QCOMPARE(rows.last().r.id, QString("ask"));
    }
    void calcBeatsAppsForTopHit() {
        QList<Result> in;
        Result a; a.id = "a"; a.kind = "app"; a.title = "a"; a.score = 1.0;
        Result c; c.id = "c"; c.kind = "calc"; c.title = "4"; c.score = 1.0;
        in << a << c;
        QCOMPARE(arrange(in, "2+2").first().r.id, QString("c"));
    }
    void mentionQueriesOnlyShowAgentsAndAsk() {
        QList<Result> in;
        const auto mk = [](QString id, QString kind, double s) { Result r; r.id = id; r.kind = kind; r.title = id; r.score = s; return r; };
        in << mk("w", "window", 0.9) << mk("m", "mention", 1.0) << mk("ask", "ask", 0.01);
        const QList<Row> rows = arrange(in, "@pa");
        QCOMPARE(rows.size(), 2);
        QCOMPARE(rows[0].r.id, QString("m"));
        QCOMPARE(rows[0].section, QString("Agents"));
    }
    void emptyQueryHasNoTopHit() {
        QList<Result> in;
        Result a; a.id = "a"; a.kind = "app"; a.title = "a"; a.score = 0.9;
        in << a;
        const QList<Row> rows = arrange(in, "");
        QCOMPARE(rows.size(), 1);
        QCOMPARE(rows.first().section, QString("Frequent"));
    }
    void sectionsAreCapped() {
        QList<Result> in;
        for (int i = 0; i < 20; ++i) { Result r; r.id = QString::number(i); r.kind = "app"; r.title = r.id; r.score = 1.0 - i * 0.01; in << r; }
        QCOMPARE(arrange(in, "x").size(), 1 + 5);   // top hit + capped Apps
    }
};
QTEST_MAIN(TestEngine)
#include "launcher_test_engine.moc"
