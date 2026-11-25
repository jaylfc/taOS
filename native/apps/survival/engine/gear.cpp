// SPDX-License-Identifier: AGPL-3.0-or-later
#include "gear.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QImageReader>
#include <QJsonDocument>
#include <QSaveFile>
#include <QUuid>
#include <cmath>

namespace Taos::Survival {

namespace {
QString newId() { return QUuid::createUuid().toString(QUuid::WithoutBraces).left(8); }
QJsonObject toJson(const GearItem &i)
{
    return {{QStringLiteral("id"), i.id}, {QStringLiteral("name"), i.name}, {QStringLiteral("category"), i.category}, {QStringLiteral("unit"), i.unit},
            {QStringLiteral("expires"), i.expires}, {QStringLiteral("lastRotated"), i.lastRotated}, {QStringLiteral("added"), i.added},
            {QStringLiteral("location"), i.location}, {QStringLiteral("photo"), i.photo}, {QStringLiteral("notes"), i.notes}, {QStringLiteral("owner"), i.owner},
            {QStringLiteral("key"), i.key}, {QStringLiteral("qty"), i.qty}, {QStringLiteral("weightG"), i.weightG}, {QStringLiteral("rotateMonths"), i.rotateMonths},
            {QStringLiteral("private"), i.isPrivate}};
}
GearItem itemFrom(const QJsonObject &o)
{
    GearItem i;
    i.id = o.value(QStringLiteral("id")).toString(); i.name = o.value(QStringLiteral("name")).toString(); i.category = o.value(QStringLiteral("category")).toString();
    i.unit = o.value(QStringLiteral("unit")).toString(); i.expires = o.value(QStringLiteral("expires")).toString(); i.lastRotated = o.value(QStringLiteral("lastRotated")).toString();
    i.added = o.value(QStringLiteral("added")).toString(); i.location = o.value(QStringLiteral("location")).toString(); i.photo = o.value(QStringLiteral("photo")).toString();
    i.notes = o.value(QStringLiteral("notes")).toString(); i.owner = o.value(QStringLiteral("owner")).toString(); i.key = o.value(QStringLiteral("key")).toString();
    i.qty = o.value(QStringLiteral("qty")).toDouble(); i.weightG = o.value(QStringLiteral("weightG")).toDouble(); i.rotateMonths = o.value(QStringLiteral("rotateMonths")).toInt();
    i.isPrivate = o.value(QStringLiteral("private")).toBool();
    return i;
}
bool matches(const GearItem &i, const ScenarioRule &r)
{
    if (!i.key.isEmpty() && i.key == r.key)
        return true;
    const QString n = i.name.toLower();
    for (const QString &m : r.match)
        if (n.contains(m))
            return true;
    return false;
}
QDate dateOf(const QString &s) { return QDate::fromString(s, Qt::ISODate); }
bool expiredOn(const GearItem &i, const QDate &today) { const QDate e = dateOf(i.expires); return e.isValid() && e < today; }
} // namespace

const QStringList &gearCategories()
{
    static const QStringList c{QStringLiteral("water"), QStringLiteral("food"), QStringLiteral("shelter"), QStringLiteral("warmth"), QStringLiteral("light"),
        QStringLiteral("power"), QStringLiteral("firstaid"), QStringLiteral("medication"), QStringLiteral("tools"), QStringLiteral("comms"),
        QStringLiteral("documents"), QStringLiteral("hygiene"), QStringLiteral("clothing")};
    return c;
}
const QStringList &gearKitTypes()
{
    static const QStringList t{QStringLiteral("gobag"), QStringLiteral("car"), QStringLiteral("home"), QStringLiteral("firstaid")};
    return t;
}

ScenarioSet loadScenarios(const QByteArray &json, QString *error)
{
    ScenarioSet set;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = QStringLiteral("The scenario list is not valid JSON.");
        return set;
    }
    for (const QJsonValue &v : d.object().value(QStringLiteral("sources")).toArray()) {
        const QJsonObject o = v.toObject();
        set.sources.append({o.value(QStringLiteral("id")).toString(), o.value(QStringLiteral("title")).toString(), o.value(QStringLiteral("publisher")).toString(),
                            o.value(QStringLiteral("url")).toString(), o.value(QStringLiteral("terms")).toString()});
    }
    for (const QJsonValue &v : d.object().value(QStringLiteral("scenarios")).toArray()) {
        const QJsonObject o = v.toObject();
        Scenario s;
        s.id = o.value(QStringLiteral("id")).toString(); s.title = o.value(QStringLiteral("title")).toString(); s.summary = o.value(QStringLiteral("summary")).toString();
        s.kitType = o.value(QStringLiteral("kitType")).toString(); s.days = o.value(QStringLiteral("days")).toInt();
        for (const QJsonValue &sv : o.value(QStringLiteral("sources")).toArray()) s.sources << sv.toString();
        for (const QJsonValue &rv : o.value(QStringLiteral("rules")).toArray()) {
            const QJsonObject r = rv.toObject();
            ScenarioRule x;
            x.key = r.value(QStringLiteral("key")).toString(); x.name = r.value(QStringLiteral("name")).toString(); x.category = r.value(QStringLiteral("category")).toString();
            x.unit = r.value(QStringLiteral("unit")).toString(); x.source = r.value(QStringLiteral("source")).toString(); x.note = r.value(QStringLiteral("note")).toString();
            x.perPerson = r.value(QStringLiteral("perPerson")).toDouble(); x.perPet = r.value(QStringLiteral("perPet")).toDouble(); x.fixed = r.value(QStringLiteral("fixed")).toDouble();
            x.isPrivate = r.value(QStringLiteral("private")).toBool();
            for (const QJsonValue &m : r.value(QStringLiteral("match")).toArray()) x.match << m.toString().toLower();
            if (!x.key.isEmpty() && !x.name.isEmpty() && gearCategories().contains(x.category) && set.source(x.source))
                s.rules.append(x);   // a rule with an unknown category or source is dropped, not guessed
        }
        if (!s.id.isEmpty() && !s.rules.isEmpty())
            set.scenarios.append(s);
    }
    return set;
}

bool GearStore::load()
{
    QFile f(path());
    if (!f.exists())
        return true;
    if (!f.open(QIODevice::ReadOnly))
        return false;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject())
        return false;   // damaged: reported, never replaced by an empty file
    const QJsonObject o = d.object();
    const QJsonObject h = o.value(QStringLiteral("household")).toObject();
    m_household = Household();
    for (const QJsonValue &v : h.value(QStringLiteral("people")).toArray()) {
        const QJsonObject p = v.toObject();
        m_household.people.append({p.value(QStringLiteral("id")).toString(), p.value(QStringLiteral("name")).toString(), p.value(QStringLiteral("kind")).toString(QStringLiteral("adult")), p.value(QStringLiteral("uid")).toInt(-1)});
    }
    m_household.pets = h.value(QStringLiteral("pets")).toInt();
    m_kits.clear();
    for (const QJsonValue &kv : o.value(QStringLiteral("kits")).toArray()) {
        const QJsonObject k = kv.toObject();
        GearKit g;
        g.id = k.value(QStringLiteral("id")).toString(); g.name = k.value(QStringLiteral("name")).toString(); g.type = k.value(QStringLiteral("type")).toString();
        g.owner = k.value(QStringLiteral("owner")).toString(); g.scenario = k.value(QStringLiteral("scenario")).toString(); g.location = k.value(QStringLiteral("location")).toString();
        g.shared = k.value(QStringLiteral("shared")).toBool();
        for (const QJsonValue &iv : k.value(QStringLiteral("items")).toArray()) g.items.append(itemFrom(iv.toObject()));
        m_kits.append(g);
    }
    m_shopping.clear();
    for (const QJsonValue &sv : o.value(QStringLiteral("shopping")).toArray()) {
        const QJsonObject s = sv.toObject();
        ShoppingList l;
        l.id = s.value(QStringLiteral("id")).toString(); l.kitId = s.value(QStringLiteral("kit")).toString(); l.scenarioId = s.value(QStringLiteral("scenario")).toString();
        l.status = s.value(QStringLiteral("status")).toString(); l.createdBy = s.value(QStringLiteral("createdBy")).toString(); l.created = s.value(QStringLiteral("created")).toString();
        l.approvedBy = s.value(QStringLiteral("approvedBy")).toString();
        for (const QJsonValue &iv : s.value(QStringLiteral("items")).toArray()) {
            const QJsonObject i = iv.toObject();
            l.items.append({i.value(QStringLiteral("name")).toString(), i.value(QStringLiteral("category")).toString(), i.value(QStringLiteral("unit")).toString(), i.value(QStringLiteral("qty")).toDouble()});
        }
        m_shopping.append(l);
    }
    m_delivered.clear();
    for (const QJsonValue &v : o.value(QStringLiteral("delivered")).toArray()) m_delivered << v.toString();
    return true;
}

bool GearStore::save(QString *error) const
{
    QDir().mkpath(m_dir);
    QJsonArray people, kits, shop;
    for (const GearPerson &p : m_household.people)
        people.append(QJsonObject{{QStringLiteral("id"), p.id}, {QStringLiteral("name"), p.name}, {QStringLiteral("kind"), p.kind}, {QStringLiteral("uid"), p.uid}});
    for (const GearKit &k : m_kits) {
        QJsonArray items;
        for (const GearItem &i : k.items) items.append(toJson(i));
        kits.append(QJsonObject{{QStringLiteral("id"), k.id}, {QStringLiteral("name"), k.name}, {QStringLiteral("type"), k.type}, {QStringLiteral("owner"), k.owner},
                                {QStringLiteral("scenario"), k.scenario}, {QStringLiteral("location"), k.location}, {QStringLiteral("shared"), k.shared}, {QStringLiteral("items"), items}});
    }
    for (const ShoppingList &l : m_shopping) {
        QJsonArray items;
        for (const ShoppingItem &i : l.items)
            items.append(QJsonObject{{QStringLiteral("name"), i.name}, {QStringLiteral("category"), i.category}, {QStringLiteral("unit"), i.unit}, {QStringLiteral("qty"), i.qty}});
        shop.append(QJsonObject{{QStringLiteral("id"), l.id}, {QStringLiteral("kit"), l.kitId}, {QStringLiteral("scenario"), l.scenarioId}, {QStringLiteral("status"), l.status},
                                {QStringLiteral("createdBy"), l.createdBy}, {QStringLiteral("created"), l.created}, {QStringLiteral("approvedBy"), l.approvedBy}, {QStringLiteral("items"), items}});
    }
    QStringList delivered = m_delivered;
    while (delivered.size() > 500) delivered.removeFirst();
    const QJsonObject o{{QStringLiteral("version"), 1}, {QStringLiteral("household"), QJsonObject{{QStringLiteral("people"), people}, {QStringLiteral("pets"), m_household.pets}}},
                        {QStringLiteral("kits"), kits}, {QStringLiteral("shopping"), shop}, {QStringLiteral("delivered"), QJsonArray::fromStringList(delivered)}};
    QSaveFile f(path());
    if (!f.open(QIODevice::WriteOnly)) { if (error) *error = f.errorString(); return false; }
    f.write(QJsonDocument(o).toJson());
    if (!f.commit()) { if (error) *error = f.errorString(); return false; }
    return true;
}

QString GearStore::personForUid(int uid) const
{
    for (const GearPerson &p : m_household.people) if (p.uid == uid && uid >= 0) return p.id;
    return {};
}
bool GearStore::linkUid(int uid, const QString &person)
{
    for (const GearPerson &p : m_household.people) if (p.uid == uid && p.id != person) return false;
    for (GearPerson &p : m_household.people) if (p.id == person) { p.uid = uid; return true; }
    return false;
}
QString GearStore::defaultPerson() const { return m_household.people.isEmpty() ? QStringLiteral("me") : m_household.people.first().id; }

QString GearStore::addKit(const QString &name, const QString &type, const QString &owner, bool shared, const QString &scenario)
{
    GearKit k;
    k.id = newId(); k.name = name.trimmed().isEmpty() ? QStringLiteral("Kit") : name.trimmed();
    k.type = gearKitTypes().contains(type) ? type : QStringLiteral("gobag");
    k.owner = owner; k.shared = shared; k.scenario = scenario;
    m_kits.append(k);
    return k.id;
}
bool GearStore::removeKit(const QString &id)
{
    for (int i = 0; i < m_kits.size(); ++i) if (m_kits[i].id == id) { m_kits.removeAt(i); return true; }
    return false;
}
GearKit *GearStore::kitMut(const QString &id) { for (GearKit &k : m_kits) if (k.id == id) return &k; return nullptr; }
const GearKit *GearStore::kit(const QString &id) const { for (const GearKit &k : m_kits) if (k.id == id) return &k; return nullptr; }
bool GearStore::setKitShared(const QString &id, bool shared, const QString &actor)
{
    GearKit *k = kitMut(id);
    if (!k || k->owner != actor)
        return false;   // only the owner changes who sees a kit
    k->shared = shared;
    return true;
}
QList<GearKit> GearStore::kitsFor(const QString &person) const
{
    QList<GearKit> out;
    for (const GearKit &k : m_kits) {
        if (!visible(k, person)) continue;
        GearKit v = k;
        v.items.clear();
        for (const GearItem &i : k.items) if (itemVisible(i, person)) v.items.append(i);
        out.append(v);
    }
    return out;
}

QString GearStore::addItem(const QString &kitId, GearItem item)
{
    GearKit *k = kitMut(kitId);
    if (!k) return {};
    item.id = newId();
    if (item.category.isEmpty() || !gearCategories().contains(item.category)) item.category = QStringLiteral("tools");
    if (item.owner.isEmpty()) item.owner = k->owner;
    if (item.category == QStringLiteral("medication")) item.isPrivate = true;   // medication is the owner's business
    if (item.added.isEmpty()) item.added = QDate::currentDate().toString(Qt::ISODate);
    if (item.qty < 0) item.qty = 0;
    k->items.append(item);
    return item.id;
}
bool GearStore::updateItem(const QString &kitId, const GearItem &item)
{
    GearKit *k = kitMut(kitId);
    if (!k) return false;
    for (GearItem &i : k->items)
        if (i.id == item.id) {
            GearItem n = item;
            if (n.category == QStringLiteral("medication")) n.isPrivate = true;
            if (!gearCategories().contains(n.category)) n.category = i.category;
            n.added = i.added; n.owner = i.owner;   // who owns it and when it came never change by editing
            if (n.qty < 0) n.qty = 0;
            i = n;
            return true;
        }
    return false;
}
bool GearStore::removeItem(const QString &kitId, const QString &itemId)
{
    GearKit *k = kitMut(kitId);
    if (!k) return false;
    for (int i = 0; i < k->items.size(); ++i) if (k->items[i].id == itemId) { k->items.removeAt(i); return true; }
    return false;
}
bool GearStore::rotate(const QString &kitId, const QString &itemId, const QDate &today, const QDate &newExpiry)
{
    GearKit *k = kitMut(kitId);
    if (!k) return false;
    for (GearItem &i : k->items)
        if (i.id == itemId) {
            i.lastRotated = today.toString(Qt::ISODate);
            if (newExpiry.isValid()) i.expires = newExpiry.toString(Qt::ISODate);
            return true;
        }
    return false;
}

bool GearStore::attachPhoto(const QString &kitId, const QString &itemId, const QString &source, QString *error)
{
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    GearKit *k = kitMut(kitId);
    if (!k) return fail(QStringLiteral("No such kit."));
    GearItem *item = nullptr;
    for (GearItem &i : k->items) if (i.id == itemId) item = &i;
    if (!item) return fail(QStringLiteral("No such item."));
    const QFileInfo fi(source);
    if (!fi.isFile() || fi.size() <= 0 || fi.size() > 8 * 1024 * 1024)
        return fail(QStringLiteral("The picture must be a file of up to 8 MB."));
    QImageReader r(source);
    const QByteArray fmt = r.format().toLower();
    if (!(fmt == "png" || fmt == "jpeg" || fmt == "jpg" || fmt == "gif" || fmt == "webp") || !r.canRead() || r.size().width() <= 0 || r.size().width() > 20000 || r.size().height() > 20000)
        return fail(QStringLiteral("That is not a picture that can be shown."));
    QDir().mkpath(m_dir + QStringLiteral("/gear-photos"));
    const QString name = QStringLiteral("gear-photos/") + itemId + QLatin1Char('.') + QString::fromLatin1(fmt == "jpeg" ? "jpg" : fmt);
    QFile::remove(m_dir + QLatin1Char('/') + name);
    if (!QFile::copy(source, m_dir + QLatin1Char('/') + name))
        return fail(QStringLiteral("The picture could not be copied into your data folder."));
    item->photo = name;
    return true;
}

QString GearStore::createKitFromScenario(const ScenarioSet &set, const QString &scenarioId, const QString &name, const QString &owner, bool shared)
{
    const Scenario *s = set.find(scenarioId);
    if (!s) return {};
    const QString id = addKit(name.isEmpty() ? s->title : name, s->kitType, owner, shared, scenarioId);
    for (const ScenarioRule &r : s->rules) {
        GearItem i;
        i.name = r.name; i.category = r.category; i.unit = r.unit; i.qty = 0; i.key = r.key; i.isPrivate = r.isPrivate; i.owner = owner;
        addItem(id, i);
    }
    return id;
}

Audit GearStore::audit(const ScenarioSet &set, const QString &kitId, const QString &scenarioId, const QString &person, const QDate &today) const
{
    Audit a;
    a.kitId = kitId; a.scenarioId = scenarioId;
    const GearKit *k = kit(kitId);
    const Scenario *s = set.find(scenarioId);
    if (!k || !s || !visible(*k, person))
        return a;
    double total = 0;
    for (const ScenarioRule &r : s->rules) {
        AuditRow row;
        row.key = r.key; row.name = r.name; row.category = r.category; row.unit = r.unit; row.note = r.note; row.source = r.source;
        row.needed = r.needed(m_household);
        for (const GearItem &i : k->items) {
            if (!itemVisible(i, person) || !matches(i, r)) continue;
            if (expiredOn(i, today)) { ++row.expiredIgnored; continue; }
            row.have += i.qty;
            row.itemIds << i.id;
        }
        row.lacking = qMax(0.0, row.needed - row.have);
        row.status = row.have <= 0 ? QStringLiteral("missing") : row.have + 1e-9 < row.needed ? QStringLiteral("short") : QStringLiteral("ok");
        total += row.needed > 0 ? qMin(1.0, row.have / row.needed) : 1.0;
        if (row.status == QStringLiteral("missing")) ++a.missing; else if (row.status == QStringLiteral("short")) ++a.shortCount; else ++a.ok;
        a.rows.append(row);
    }
    a.score = a.rows.isEmpty() ? 0 : int(std::lround(total / a.rows.size() * 100.0));
    return a;
}

int GearStore::expiredCount(const QString &person, const QDate &today) const
{
    int n = 0;
    for (const GearKit &k : kitsFor(person)) for (const GearItem &i : k.items) if (expiredOn(i, today)) ++n;
    return n;
}

QList<Reminder> GearStore::reminders(const QString &person, const QDate &today, int windowDays) const
{
    QList<Reminder> out;
    for (const GearKit &k : kitsFor(person)) {
        for (const GearItem &i : k.items) {
            const QDate e = dateOf(i.expires);
            if (e.isValid()) {
                const int left = int(today.daysTo(e));
                if (left < 0)
                    out.append({QStringLiteral("expired:") + i.id + QLatin1Char(':') + i.expires, QStringLiteral("expired"), k.id, i.id, i.name + QStringLiteral(" has expired"),
                                QStringLiteral("In %1. It ran out on %2. Replace it.").arg(k.name, i.expires), i.expires, left});
                else if (left <= windowDays)
                    out.append({QStringLiteral("expiring:") + i.id + QLatin1Char(':') + i.expires, QStringLiteral("expiring"), k.id, i.id, i.name + QStringLiteral(" expires soon"),
                                QStringLiteral("In %1. Best before %2 (%3 days).").arg(k.name, i.expires).arg(left), i.expires, left});
            }
            if (i.rotateMonths > 0) {
                const QDate base = i.lastRotated.isEmpty() ? dateOf(i.added) : dateOf(i.lastRotated);
                if (!base.isValid()) continue;
                const QDate due = base.addMonths(i.rotateMonths);
                const int left = int(today.daysTo(due));
                if (left <= windowDays)
                    out.append({QStringLiteral("rotation:") + i.id + QLatin1Char(':') + due.toString(Qt::ISODate), QStringLiteral("rotation"), k.id, i.id, i.name + QStringLiteral(" is due for a swap"),
                                QStringLiteral("In %1. Use the old one and buy a new one by %2.").arg(k.name, due.toString(Qt::ISODate)), due.toString(Qt::ISODate), left});
            }
        }
    }
    std::sort(out.begin(), out.end(), [](const Reminder &a, const Reminder &b) { return a.daysLeft < b.daysLeft; });
    return out;
}

int GearStore::deliver(ReminderSink *sink, const QString &person, const QDate &today)
{
    int n = 0;
    for (const Reminder &r : reminders(person, today)) {
        if (m_delivered.contains(r.id)) continue;
        sink->notify(r);
        m_delivered << r.id;
        ++n;
    }
    return n;
}

QJsonObject GearStore::readiness(const ScenarioSet &set, const QString &person, const QDate &today) const
{
    QJsonArray kits;
    double sum = 0;
    int counted = 0, missing = 0;
    QString worstKit, worstRow;
    int worst = 101;
    for (const GearKit &k : kitsFor(person)) {
        QString sc = k.scenario;
        if (sc.isEmpty())
            for (const Scenario &s : set.scenarios) if (s.kitType == k.type) { sc = s.id; break; }
        if (sc.isEmpty() || !set.find(sc)) continue;
        const Audit a = audit(set, k.id, sc, person, today);
        kits.append(QJsonObject{{QStringLiteral("id"), k.id}, {QStringLiteral("name"), k.name}, {QStringLiteral("type"), k.type}, {QStringLiteral("scenario"), sc},
                                {QStringLiteral("score"), a.score}, {QStringLiteral("missing"), a.missing}, {QStringLiteral("short"), a.shortCount}});
        sum += a.score; ++counted; missing += a.missing;
        if (a.score < worst) {
            worst = a.score; worstKit = k.name;
            worstRow.clear();
            for (const AuditRow &r : a.rows) if (r.status == QStringLiteral("missing")) { worstRow = r.name; break; }
        }
    }
    const QList<Reminder> rem = reminders(person, today);
    int expiring = 0, expired = 0;
    for (const Reminder &r : rem) { if (r.kind == QStringLiteral("expired")) ++expired; else if (r.kind == QStringLiteral("expiring")) ++expiring; }
    QString next = counted == 0 ? QStringLiteral("Make your first kit.") : expired > 0 ? QStringLiteral("Replace what has expired.") : !worstRow.isEmpty() ? QStringLiteral("Add %1 to %2.").arg(worstRow, worstKit)
                 : expiring > 0 ? QStringLiteral("Something expires soon.") : QStringLiteral("Nothing urgent.");
    return {{QStringLiteral("score"), counted ? int(std::lround(sum / counted)) : 0}, {QStringLiteral("kitsCounted"), counted}, {QStringLiteral("kits"), kits},
            {QStringLiteral("expiring"), expiring}, {QStringLiteral("expired"), expired}, {QStringLiteral("missing"), missing}, {QStringLiteral("next"), next}};
}

QString GearStore::proposeShopping(const ScenarioSet &set, const QString &kitId, const QString &scenarioId, const QString &actor, const QString &person, const QDate &today)
{
    const Audit a = audit(set, kitId, scenarioId, person, today);
    if (a.rows.isEmpty())
        return {};
    ShoppingList l;
    l.id = newId(); l.kitId = kitId; l.scenarioId = scenarioId; l.status = QStringLiteral("draft"); l.createdBy = actor;
    l.created = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    for (const AuditRow &r : a.rows)
        if (r.lacking > 1e-9)
            l.items.append({r.name, r.category, r.unit, std::ceil(r.lacking * 10.0) / 10.0});
    if (l.items.isEmpty())
        return {};
    m_shopping.append(l);
    return l.id;
}
bool GearStore::approveShopping(const QString &id, const QString &actor, bool interactive, QString *error)
{
    if (actor.startsWith(QStringLiteral("agent")) || !interactive) {
        if (error) *error = QStringLiteral("An agent can propose a shopping list; a person approves it, by pressing Approve in the app (or with the one-time code that press makes).");
        return false;
    }
    for (ShoppingList &l : m_shopping)
        if (l.id == id) { l.status = QStringLiteral("approved"); l.approvedBy = actor; return true; }
    if (error) *error = QStringLiteral("No such list.");
    return false;
}
bool GearStore::removeShopping(const QString &id)
{
    for (int i = 0; i < m_shopping.size(); ++i) if (m_shopping[i].id == id) { m_shopping.removeAt(i); return true; }
    return false;
}

} // namespace Taos::Survival
