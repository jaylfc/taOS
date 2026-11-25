// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The gear manager: kits (go-bags, a car kit, home supplies, a first aid kit) hold items; a household of people and
// pets sets how much of each is needed; scenario templates from public guidance show what is missing. Expiry and
// rotation turn into reminders. Everything is plain data in one JSON file in the person's data folder, offline.
//
// Privacy: a kit belongs to one person and may be shared with the household. Inside a shared kit an item marked
// private (medication always is) is seen only by its owner, and an audit only counts what the viewer may see.
#include <QDate>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QString>
#include <QStringList>

namespace Taos::Survival {

const QStringList &gearCategories();   // water food shelter warmth light power firstaid medication tools comms documents hygiene clothing
const QStringList &gearKitTypes();     // gobag car home firstaid

struct GearPerson { QString id, name, kind; };   // kind: adult | child
struct Household
{
    QList<GearPerson> people;
    int pets = 0;
    int peopleCount() const { return qMax(1, int(people.size())); }
};

struct GearItem
{
    QString id, name, category, unit, expires, lastRotated, added, location, photo, notes, owner, key;
    double qty = 0;
    double weightG = 0;          // one unit
    int rotateMonths = 0;
    bool isPrivate = false;
};

struct GearKit
{
    QString id, name, type, owner, scenario, location;
    bool shared = false;
    QList<GearItem> items;
    double weightG() const { double w = 0; for (const GearItem &i : items) w += i.weightG * i.qty; return w; }
};

struct ScenarioRule
{
    QString key, name, category, unit, source, note;
    double perPerson = 0, perPet = 0, fixed = 0;
    QStringList match;
    bool isPrivate = false;
    double needed(const Household &h) const { return perPerson * h.peopleCount() + perPet * h.pets + fixed; }
};
struct ScenarioSource { QString id, title, publisher, url, terms; };
struct Scenario
{
    QString id, title, summary, kitType;
    int days = 0;
    QStringList sources;
    QList<ScenarioRule> rules;
};
struct ScenarioSet
{
    QList<ScenarioSource> sources;
    QList<Scenario> scenarios;
    const Scenario *find(const QString &id) const { for (const Scenario &s : scenarios) if (s.id == id) return &s; return nullptr; }
    const ScenarioSource *source(const QString &id) const { for (const ScenarioSource &s : sources) if (s.id == id) return &s; return nullptr; }
};
ScenarioSet loadScenarios(const QByteArray &json, QString *error = nullptr);

struct AuditRow
{
    QString key, name, category, unit, status, note, source;   // status: ok | short | missing
    double needed = 0, have = 0, lacking = 0;
    QStringList itemIds;                                         // what counted
    int expiredIgnored = 0;                                      // items left out because they are past their date
};
struct Audit
{
    QString kitId, scenarioId;
    QList<AuditRow> rows;
    int score = 0;               // 0..100
    int missing = 0, shortCount = 0, ok = 0;
};

struct Reminder
{
    QString id, kind, kitId, itemId, title, body, due;   // kind: expired | expiring | rotation
    int daysLeft = 0;
};
class ReminderSink
{
public:
    virtual ~ReminderSink() = default;
    virtual void notify(const Reminder &r) = 0;
};

struct ShoppingItem { QString name, category, unit; double qty = 0; };
struct ShoppingList
{
    QString id, kitId, scenarioId, status, createdBy, created, approvedBy;   // status: draft | approved
    QList<ShoppingItem> items;
};

class GearStore
{
public:
    explicit GearStore(QString dataDir) : m_dir(std::move(dataDir)) {}
    bool load();                                  // false: the file is damaged and is left as it is
    bool save(QString *error = nullptr) const;
    QString path() const { return m_dir + QStringLiteral("/gear.json"); }

    Household household() const { return m_household; }
    void setHousehold(const Household &h) { m_household = h; }
    QString defaultPerson() const;

    QString addKit(const QString &name, const QString &type, const QString &owner, bool shared, const QString &scenario = QString());
    bool removeKit(const QString &id);
    bool setKitShared(const QString &id, bool shared, const QString &actor);
    QList<GearKit> kitsFor(const QString &person) const;        // owned or shared, items filtered for this person
    const GearKit *kit(const QString &id) const;
    /// Adds an item (an id is made) and returns it; medication is private to its owner unless said otherwise.
    QString addItem(const QString &kitId, GearItem item);
    bool updateItem(const QString &kitId, const GearItem &item);
    bool removeItem(const QString &kitId, const QString &itemId);
    bool rotate(const QString &kitId, const QString &itemId, const QDate &today, const QDate &newExpiry = QDate());
    /// Copies a picture into the data folder (png, jpeg, webp, gif up to 8 MB that really decodes) and keeps its name.
    bool attachPhoto(const QString &kitId, const QString &itemId, const QString &source, QString *error = nullptr);
    /// A kit with every rule of the scenario as an item at quantity 0, to be filled in.
    QString createKitFromScenario(const ScenarioSet &set, const QString &scenarioId, const QString &name, const QString &owner, bool shared);

    Audit audit(const ScenarioSet &set, const QString &kitId, const QString &scenarioId, const QString &person, const QDate &today) const;
    int expiredCount(const QString &person, const QDate &today) const;
    QList<Reminder> reminders(const QString &person, const QDate &today, int windowDays = 30) const;
    /// Calls the sink for reminders not delivered before and remembers them; returns how many.
    int deliver(ReminderSink *sink, const QString &person, const QDate &today);
    /// {score, kits:[{id,name,type,score,missing}], expiring, expired, missing, next}
    QJsonObject readiness(const ScenarioSet &set, const QString &person, const QDate &today) const;

    /// A shopping list of what the audit lacks. `actor` is who proposes it ("agent:<name>" or a person id).
    QString proposeShopping(const ScenarioSet &set, const QString &kitId, const QString &scenarioId, const QString &actor, const QString &person, const QDate &today);
    /// Only a person approves: an actor that starts with "agent" is refused.
    bool approveShopping(const QString &id, const QString &actor, QString *error = nullptr);
    QList<ShoppingList> shopping() const { return m_shopping; }
    bool removeShopping(const QString &id);

private:
    GearKit *kitMut(const QString &id);
    static bool visible(const GearKit &k, const QString &person) { return k.owner == person || k.shared; }
    static bool itemVisible(const GearItem &i, const QString &person) { return !i.isPrivate || i.owner == person; }
    QString m_dir;
    Household m_household;
    QList<GearKit> m_kits;
    QList<ShoppingList> m_shopping;
    QStringList m_delivered;
};

} // namespace Taos::Survival
