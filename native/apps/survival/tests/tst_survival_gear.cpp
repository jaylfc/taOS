// SPDX-License-Identifier: AGPL-3.0-or-later
// The gear manager: scenario lists scaled to the household, what is missing, expiry and rotation reminders, who may
// see what (medication stays with its owner), agent proposals that only a person approves, and the saved file.
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QFile>
#include <QImage>
#include <QTemporaryDir>
#include <QtTest>

#include "gear.h"

using namespace Taos::Survival;
using namespace Qt::StringLiterals;

namespace {
const QDate kToday(2026, 11, 25);
struct CountSink : ReminderSink
{
    QStringList ids;
    void notify(const Reminder &r) override { ids << r.id; }
};
ScenarioSet realScenarios()
{
    QFile f(QStringLiteral(SURVIVAL_CATALOG_DIR "/scenarios.json"));
    f.open(QIODevice::ReadOnly);
    return loadScenarios(f.readAll());
}
QByteArray readData(const char *n)
{
    QFile f(QStringLiteral(SURVIVAL_TEST_DATA "/") + QLatin1String(n));
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}
GearItem item(const QString &name, const QString &cat, double qty, const QString &unit = QString(), const QString &expires = QString())
{
    GearItem i;
    i.name = name; i.category = cat; i.qty = qty; i.unit = unit; i.expires = expires;
    return i;
}
Household family() { Household h; h.people = {{u"ann"_s, u"Ann"_s, u"adult"_s}, {u"bo"_s, u"Bo"_s, u"adult"_s}, {u"cy"_s, u"Cy"_s, u"child"_s}}; h.pets = 1; return h; }
}

class TstSurvivalGear : public QObject
{
    Q_OBJECT
    QTemporaryDir m_dir;

private Q_SLOTS:
    void initTestCase() { QVERIFY(m_dir.isValid()); }

    void scenarios_are_complete_and_every_rule_cites_a_source()
    {
        const ScenarioSet set = realScenarios();
        QCOMPARE(set.scenarios.size(), 5);
        for (const QString &id : {u"72h"_s, u"power-cut"_s, u"evacuation"_s, u"winter-car"_s, u"first-aid"_s})
            QVERIFY2(set.find(id), qPrintable(id));
        for (const Scenario &s : set.scenarios) {
            QVERIFY(!s.sources.isEmpty());
            for (const QString &sid : s.sources) QVERIFY2(set.source(sid), qPrintable(sid));
            for (const ScenarioRule &r : s.rules) {
                QVERIFY2(gearCategories().contains(r.category), qPrintable(r.key));
                QVERIFY2(set.source(r.source), qPrintable(r.key));
                QVERIFY2(r.perPerson + r.perPet + r.fixed > 0, qPrintable(r.key));
                QVERIFY2(!r.match.isEmpty(), qPrintable(r.key));
            }
        }
        for (const ScenarioSource &s : set.sources) {
            QVERIFY(!s.terms.isEmpty());   // every source says what we may do with it
            QVERIFY(s.id == u"taos"_s || s.url.startsWith(u"https://"_s));
        }
        QVERIFY(set.source(u"uk-beready"_s)->terms.contains(u"Open Government Licence"_s));
        // a rule with an unknown category or an unknown source is dropped, never guessed
        const ScenarioSet bad = loadScenarios(readData("bad-scenarios.json"));
        QCOMPARE(bad.scenarios.first().rules.size(), 1);
        QVERIFY(loadScenarios("{broken").scenarios.isEmpty());
    }
    void quantities_scale_with_people_and_pets()
    {
        const ScenarioSet set = realScenarios();
        const Scenario *s = set.find(u"72h"_s);
        Household h = family();
        double water = 0, pet = 0, radio = 0;
        for (const ScenarioRule &r : s->rules) {
            if (r.key == u"water"_s) water = r.needed(h);
            if (r.key == u"petfood"_s) pet = r.needed(h);
            if (r.key == u"radio"_s) radio = r.needed(h);
        }
        QVERIFY(qAbs(water - 34.2) < 1e-6);   // 3.8 L x 3 days x 3 people
        QCOMPARE(pet, 3.0);                    // one pet
        QCOMPARE(radio, 1.0);                  // one radio however many people
        h.pets = 0;
        for (const ScenarioRule &r : s->rules) if (r.key == u"petfood"_s) QCOMPARE(r.needed(h), 0.0);
        Household none;   // nobody listed counts as one person
        QCOMPARE(none.peopleCount(), 1);
    }
    void audit_says_missing_short_ok_and_ignores_expired_stock()
    {
        const ScenarioSet set = realScenarios();
        GearStore st(m_dir.path() + u"/a"_s);
        st.setHousehold(family());
        const QString k = st.addKit(u"Go bag"_s, u"gobag"_s, u"ann"_s, false);
        st.addItem(k, item(u"Bottled water"_s, u"water"_s, 20, u"L"_s));                       // 34.2 needed: short
        st.addItem(k, item(u"Torch"_s, u"light"_s, 3));                                          // 3 people: ok
        st.addItem(k, item(u"Hand crank radio"_s, u"comms"_s, 1, QString(), u"2026-01-01"_s));   // expired: does not count
        const Audit a = st.audit(set, k, u"72h"_s, u"ann"_s, kToday);
        auto row = [&](const QString &key) { for (const AuditRow &r : a.rows) if (r.key == key) return r; return AuditRow(); };
        QCOMPARE(row(u"water"_s).status, u"short"_s);
        QVERIFY(qAbs(row(u"water"_s).lacking - 14.2) < 1e-6);
        QCOMPARE(row(u"torch"_s).status, u"ok"_s);
        QCOMPARE(row(u"radio"_s).status, u"missing"_s);
        QCOMPARE(row(u"radio"_s).expiredIgnored, 1);
        QCOMPARE(row(u"firstaid"_s).status, u"missing"_s);
        QVERIFY(a.score > 0 && a.score < 25);
        QCOMPARE(a.ok + a.shortCount + a.missing, int(a.rows.size()));
        // an item can say which rule it fills, whatever it is called
        GearItem named = item(u"Blue thing"_s, u"comms"_s, 1);
        named.key = u"radio"_s;
        st.addItem(k, named);
        const Audit b = st.audit(set, k, u"72h"_s, u"ann"_s, kToday);
        for (const AuditRow &r : b.rows) if (r.key == u"radio"_s) QCOMPARE(r.status, u"ok"_s);
    }
    void a_fully_stocked_kit_scores_100()
    {
        const ScenarioSet set = realScenarios();
        GearStore st(m_dir.path() + u"/full"_s);
        Household h; h.people = {{u"ann"_s, u"Ann"_s, u"adult"_s}};
        st.setHousehold(h);
        const QString k = st.createKitFromScenario(set, u"winter-car"_s, QString(), u"ann"_s, false);
        QVERIFY(!k.isEmpty());
        QCOMPARE(st.audit(set, k, u"winter-car"_s, u"ann"_s, kToday).score, 0);   // everything listed, nothing yet in hand
        GearKit kit = *st.kit(k);
        for (const GearItem &i : kit.items) {
            GearItem x = i;
            for (const ScenarioRule &r : set.find(u"winter-car"_s)->rules) if (r.key == i.key) x.qty = r.needed(h);
            QVERIFY(st.updateItem(k, x));
        }
        QCOMPARE(st.audit(set, k, u"winter-car"_s, u"ann"_s, kToday).score, 100);
        QCOMPARE(st.readiness(set, u"ann"_s, kToday).value(u"score"_s).toInt(), 100);
    }
    void private_items_and_kits_stay_with_their_owner()
    {
        const ScenarioSet set = realScenarios();
        GearStore st(m_dir.path() + u"/priv"_s);
        st.setHousehold(family());
        const QString mine = st.addKit(u"Ann's bag"_s, u"gobag"_s, u"ann"_s, false);
        const QString shared = st.addKit(u"Home"_s, u"home"_s, u"ann"_s, true);
        GearItem meds = item(u"Insulin"_s, u"medication"_s, 2);
        meds.isPrivate = false;   // asking for it to be public is overridden: medication is private
        st.addItem(shared, meds);
        st.addItem(shared, item(u"Torch"_s, u"light"_s, 3));
        QCOMPARE(st.kitsFor(u"bo"_s).size(), 1);                 // Bo sees only the shared kit
        QCOMPARE(st.kitsFor(u"bo"_s).first().id, shared);
        QCOMPARE(st.kitsFor(u"bo"_s).first().items.size(), 1);   // and not the insulin
        for (const GearKit &k : st.kitsFor(u"ann"_s)) if (k.id == shared) QCOMPARE(k.items.size(), 2);   // the owner sees everything of hers
        QCOMPARE(st.kitsFor(u"ann"_s).size(), 2);
        // Bo's audit does not count Ann's medication, and Bo cannot audit Ann's own bag
        const Audit ab = st.audit(set, shared, u"power-cut"_s, u"bo"_s, kToday);
        for (const AuditRow &r : ab.rows) if (r.key == u"meds"_s) QCOMPARE(r.have, 0.0);
        QVERIFY(st.audit(set, mine, u"72h"_s, u"bo"_s, kToday).rows.isEmpty());
        // only the owner decides who sees a kit
        QVERIFY(!st.setKitShared(mine, true, u"bo"_s));
        QVERIFY(st.setKitShared(mine, true, u"ann"_s));
        QCOMPARE(st.kitsFor(u"bo"_s).size(), 2);
        // reminders respect the same wall
        GearItem old = item(u"Insulin pen"_s, u"medication"_s, 1, QString(), u"2026-11-30"_s);
        st.addItem(shared, old);
        for (const Reminder &r : st.reminders(u"bo"_s, kToday)) QVERIFY(!r.title.contains(u"Insulin"_s));
        bool annSees = false;
        for (const Reminder &r : st.reminders(u"ann"_s, kToday)) annSees |= r.title.contains(u"Insulin"_s);
        QVERIFY(annSees);
    }
    void expiry_and_rotation_become_reminders_once()
    {
        GearStore st(m_dir.path() + u"/rem"_s);
        st.setHousehold(family());
        const QString k = st.addKit(u"Car"_s, u"car"_s, u"ann"_s, false);
        st.addItem(k, item(u"Old water"_s, u"water"_s, 1, QString(), u"2026-11-01"_s));          // expired
        st.addItem(k, item(u"Snack bars"_s, u"food"_s, 4, QString(), u"2026-12-10"_s));          // in 15 days
        st.addItem(k, item(u"Far food"_s, u"food"_s, 4, QString(), u"2027-06-01"_s));            // far away: no reminder
        GearItem batt = item(u"Batteries"_s, u"power"_s, 1);
        batt.rotateMonths = 12; batt.lastRotated = u"2025-12-01"_s;                              // swap due 2026-12-01
        st.addItem(k, batt);
        GearItem fresh = item(u"Radio"_s, u"comms"_s, 1);
        fresh.rotateMonths = 12; fresh.lastRotated = u"2026-10-01"_s;                            // due next October: nothing
        st.addItem(k, fresh);
        const auto rem = st.reminders(u"ann"_s, kToday);
        QStringList kinds;
        for (const Reminder &r : rem) kinds << r.kind + u":"_s + r.title;
        QVERIFY2(kinds.contains(u"expired:Old water has expired"_s), qPrintable(kinds.join(u"|"_s)));
        QVERIFY(kinds.contains(u"expiring:Snack bars expires soon"_s));
        QVERIFY(kinds.contains(u"rotation:Batteries is due for a swap"_s));
        QCOMPARE(rem.size(), 3);
        QCOMPARE(rem.first().kind, u"expired"_s);   // most urgent first
        QCOMPARE(st.expiredCount(u"ann"_s, kToday), 1);
        // delivered once, and it stays delivered after a restart
        CountSink sink;
        QCOMPARE(st.deliver(&sink, u"ann"_s, kToday), 3);
        QCOMPARE(st.deliver(&sink, u"ann"_s, kToday), 0);
        QString err;
        QVERIFY2(st.save(&err), qPrintable(err));
        GearStore again(m_dir.path() + u"/rem"_s);
        QVERIFY(again.load());
        CountSink sink2;
        QCOMPARE(again.deliver(&sink2, u"ann"_s, kToday), 0);
        // rotating an item clears its reminder
        QVERIFY(again.rotate(k, again.kit(k)->items[3].id, kToday, QDate(2027, 12, 1)));
        QCOMPARE(again.reminders(u"ann"_s, kToday).size(), 2);
    }
    void readiness_feeds_the_home_view()
    {
        const ScenarioSet set = realScenarios();
        GearStore st(m_dir.path() + u"/ready"_s);
        st.setHousehold(family());
        const QJsonObject none = st.readiness(set, u"ann"_s, kToday);
        QCOMPARE(none.value(u"score"_s).toInt(), 0);
        QVERIFY(none.value(u"next"_s).toString().contains(u"first kit"_s));
        const QString k = st.createKitFromScenario(set, u"72h"_s, u"Bag"_s, u"ann"_s, false);
        st.addItem(k, item(u"Water"_s, u"water"_s, 5, u"L"_s, u"2026-01-01"_s));
        const QJsonObject r = st.readiness(set, u"ann"_s, kToday);
        QCOMPARE(r.value(u"kitsCounted"_s).toInt(), 1);
        QVERIFY(r.value(u"missing"_s).toInt() > 10);
        QVERIFY(r.value(u"expired"_s).toInt() >= 1);
        QVERIFY(r.value(u"next"_s).toString().contains(u"expired"_s));   // the urgent thing first
    }
    void agent_proposals_are_drafts_only_a_person_approves()
    {
        const ScenarioSet set = realScenarios();
        GearStore st(m_dir.path() + u"/shop"_s);
        st.setHousehold(family());
        const QString k = st.addKit(u"Bag"_s, u"gobag"_s, u"ann"_s, false);
        st.addItem(k, item(u"Water"_s, u"water"_s, 20, u"L"_s));
        const QString id = st.proposeShopping(set, k, u"72h"_s, u"agent:helper"_s, u"ann"_s, kToday);
        QVERIFY(!id.isEmpty());
        ShoppingList l = st.shopping().first();
        QCOMPARE(l.status, u"draft"_s);
        QCOMPARE(l.createdBy, u"agent:helper"_s);
        bool water = false;
        for (const ShoppingItem &i : l.items) if (i.name == u"Drinking water"_s) { water = true; QVERIFY(qAbs(i.qty - 14.2) < 0.11); }
        QVERIFY(water);
        QString err;
        QVERIFY(!st.approveShopping(id, u"agent:helper"_s, &err));
        QVERIFY(err.contains(u"person"_s));
        QCOMPARE(st.shopping().first().status, u"draft"_s);
        QVERIFY(st.approveShopping(id, u"ann"_s, &err));
        QCOMPARE(st.shopping().first().status, u"approved"_s);
        QCOMPARE(st.shopping().first().approvedBy, u"ann"_s);
        // nothing lacking means no list
        GearStore full(m_dir.path() + u"/shop2"_s);
        QVERIFY(full.proposeShopping(set, u"nokit"_s, u"72h"_s, u"ann"_s, u"ann"_s, kToday).isEmpty());
        QVERIFY(st.removeShopping(id));
    }
    void weight_photo_and_file_round_trip()
    {
        GearStore st(m_dir.path() + u"/misc"_s);
        const QString k = st.addKit(u"Bag"_s, u"gobag"_s, u"ann"_s, false);
        GearItem a = item(u"Stove"_s, u"tools"_s, 1); a.weightG = 350;
        GearItem b = item(u"Gas can"_s, u"tools"_s, 2); b.weightG = 220;
        const QString ia = st.addItem(k, a);
        st.addItem(k, b);
        QCOMPARE(st.kit(k)->weightG(), 790.0);
        QImage img(8, 8, QImage::Format_RGB32); img.fill(Qt::red);
        const QString png = m_dir.filePath(u"pic.png"_s);
        QVERIFY(img.save(png));
        QString err;
        QVERIFY2(st.attachPhoto(k, ia, png, &err), qPrintable(err));
        QVERIFY(QFile::exists(m_dir.path() + u"/misc/"_s + st.kit(k)->items[0].photo));
        QFile fake(m_dir.filePath(u"fake.png"_s)); fake.open(QIODevice::WriteOnly); fake.write("not an image"); fake.close();
        QVERIFY(!st.attachPhoto(k, ia, m_dir.filePath(u"fake.png"_s), &err));
        QVERIFY(!st.attachPhoto(k, ia, m_dir.filePath(u"nothere.png"_s), &err));
        QVERIFY(!st.attachPhoto(k, u"nope"_s, png, &err));
        Household h = family();
        st.setHousehold(h);
        QVERIFY2(st.save(&err), qPrintable(err));
        GearStore again(m_dir.path() + u"/misc"_s);
        QVERIFY(again.load());
        QCOMPARE(again.household().people.size(), 3);
        QCOMPARE(again.household().pets, 1);
        QCOMPARE(again.kit(k)->items.size(), 2);
        QCOMPARE(again.kit(k)->weightG(), 790.0);
        QVERIFY(!again.kit(k)->items[0].photo.isEmpty());
        QVERIFY(again.removeItem(k, ia));
        QVERIFY(!again.removeItem(k, ia));
        QVERIFY(again.removeKit(k));
        QVERIFY(!again.removeKit(k));
    }
    void the_cli_is_the_agent_path_to_everything()
    {
        const QString data = m_dir.path() + u"/cli"_s;
        auto run = [&](const QStringList &args, const QString &actor, int *rc) {
            QProcess p;
            QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
            env.insert(u"TAOS_SURVIVAL_DIR"_s, data); env.insert(u"TAOS_SURVIVAL_TODAY"_s, u"2026-11-25"_s); env.insert(u"QT_QPA_PLATFORM"_s, u"offscreen"_s);
            if (!actor.isEmpty()) env.insert(u"TAOS_SURVIVAL_ACTOR"_s, actor); else env.remove(u"TAOS_SURVIVAL_ACTOR"_s);
            p.setProcessEnvironment(env);
            p.start(QStringLiteral(SURVIVAL_CLI), QStringList{u"gear"_s} + args);
            p.waitForFinished(30000);
            if (rc) *rc = p.exitCode();
            return QString::fromUtf8(p.readAllStandardOutput()).trimmed();
        };
        int rc = 0;
        const QString kit = run({u"from"_s, u"72h"_s, u"Ann bag"_s}, QString(), &rc);
        QCOMPARE(rc, 0);
        QVERIFY(!kit.isEmpty());
        QVERIFY(!run({u"item-add"_s, kit, u"Bottled water"_s, u"water"_s, u"6"_s, u"L"_s, u"2027-01-31"_s}, QString(), &rc).isEmpty());
        run({u"item-add"_s, kit, u"Water"_s, u"nonsense"_s, u"1"_s}, QString(), &rc);
        QVERIFY(rc != 0);
        run({u"item-add"_s, kit, u"Water"_s, u"water"_s, u"1"_s, u"L"_s, u"31/01/2027"_s}, QString(), &rc);
        QVERIFY(rc != 0);
        const QJsonObject audit = QJsonDocument::fromJson(run({u"audit"_s, kit, u"72h"_s}, QString(), &rc).toUtf8()).object();
        QCOMPARE(rc, 0);
        QVERIFY(audit.value(u"missing"_s).toInt() > 10);
        QVERIFY(QJsonDocument::fromJson(run({u"readiness"_s}, QString(), &rc).toUtf8()).object().contains(u"score"_s));
        QVERIFY(run({u"lists"_s}, QString(), &rc).contains(u"Open Government Licence"_s));
        // an agent drafts; the same agent cannot approve; a person can
        const QString shop = run({u"shop"_s, u"propose"_s, kit, u"72h"_s}, u"agent:helper"_s, &rc);
        QCOMPARE(rc, 0);
        QVERIFY(run({u"shop"_s, u"list"_s}, QString(), &rc).contains(u"draft"_s));
        run({u"shop"_s, u"approve"_s, shop}, u"agent:helper"_s, &rc);
        QVERIFY(rc != 0);
        run({u"shop"_s, u"approve"_s, shop}, QString(), &rc);
        QCOMPARE(rc, 0);
        QVERIFY(run({u"shop"_s, u"list"_s}, QString(), &rc).contains(u"approved"_s));
    }
    void a_damaged_file_is_reported_and_never_overwritten()
    {
        QDir().mkpath(m_dir.path() + u"/bad"_s);
        QFile f(m_dir.path() + u"/bad/gear.json"_s);
        QVERIFY(f.open(QIODevice::WriteOnly)); f.write("{ broken"); f.close();
        GearStore st(m_dir.path() + u"/bad"_s);
        QVERIFY(!st.load());
        QFile g(m_dir.path() + u"/bad/gear.json"_s);
        QVERIFY(g.open(QIODevice::ReadOnly));
        QCOMPARE(g.readAll(), QByteArray("{ broken"));
    }
};

QTEST_MAIN(TstSurvivalGear)
#include "tst_survival_gear.moc"
