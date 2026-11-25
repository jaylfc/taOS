// SPDX-License-Identifier: AGPL-3.0-or-later
#include "gearengine.h"

#include <QLocale>
#include <QUrl>

#include "backend.h"
#include "paths.h"

namespace Taos::Survival {

using namespace Qt::StringLiterals;

namespace {
QVariantMap itemMap(const GearItem &i, const QDate &today)
{
    const QDate e = QDate::fromString(i.expires, Qt::ISODate);
    const int left = e.isValid() ? int(today.daysTo(e)) : 99999;
    return {{u"id"_s, i.id}, {u"name"_s, i.name}, {u"category"_s, i.category}, {u"qty"_s, i.qty}, {u"unit"_s, i.unit}, {u"expires"_s, i.expires},
            {u"daysLeft"_s, left}, {u"expiry"_s, !e.isValid() ? u"none"_s : left < 0 ? u"expired"_s : left <= 30 ? u"soon"_s : u"ok"_s},
            {u"rotateMonths"_s, i.rotateMonths}, {u"lastRotated"_s, i.lastRotated}, {u"location"_s, i.location}, {u"weightG"_s, i.weightG}, {u"notes"_s, i.notes},
            {u"photo"_s, i.photo}, {u"private"_s, i.isPrivate}, {u"owner"_s, i.owner}, {u"key"_s, i.key}};
}
QString weightText(double g)
{
    if (g <= 0) return QString();
    return g >= 1000 ? u"%1 kg"_s.arg(g / 1000.0, 0, 'f', 1) : u"%1 g"_s.arg(qRound(g));
}
QString qtyText(double q) { return qAbs(q - qRound(q)) < 1e-9 ? QString::number(qRound(q)) : QString::number(q, 'f', 1); }
} // namespace

GearEngine::GearEngine(const QString &dataDir, QObject *parent) : QObject(parent), m_dir(dataDir), m_store(dataDir)
{
    QString err;
    m_set = loadScenarios(bundledCatalog(u"scenarios.json"_s), &err);
    if (!m_store.load()) {
        m_error = tr("Your gear file could not be read, so nothing was changed. It is %1").arg(m_store.path());
        return;
    }
    if (m_store.household().people.isEmpty()) {   // a household of one to start with
        Household h = m_store.household();
        h.people.append({u"me"_s, tr("Me"), u"adult"_s});
        m_store.setHousehold(h);
    }
    m_me = qEnvironmentVariable("TAOS_SURVIVAL_PERSON");
    if (m_me.isEmpty()) m_me = m_store.defaultPerson();
}

QDate GearEngine::today() const
{
    const QDate d = QDate::fromString(qEnvironmentVariable("TAOS_SURVIVAL_TODAY"), Qt::ISODate);
    return d.isValid() ? d : QDate::currentDate();
}

void GearEngine::persist()
{
    QString err;
    m_error = m_store.save(&err) ? QString() : tr("Your gear could not be saved: %1").arg(err);
}
void GearEngine::after() { persist(); Q_EMIT changed(); if (!m_auditScenario.isEmpty()) runAudit(m_auditScenario); }

QVariantList GearEngine::people() const
{
    QVariantList out;
    for (const GearPerson &p : m_store.household().people)
        out.append(QVariantMap{{u"id"_s, p.id}, {u"name"_s, p.name}, {u"kind"_s, p.kind}});
    return out;
}
void GearEngine::setMe(const QString &id) { m_me = id; m_selected.clear(); m_audit.clear(); m_auditScenario.clear(); Q_EMIT changed(); Q_EMIT auditChanged(); }
QString GearEngine::addPerson(const QString &name, const QString &kind)
{
    Household h = m_store.household();
    const QString n = name.trimmed();
    if (n.isEmpty()) return {};
    GearPerson p{QString::number(qHash(n + QString::number(h.people.size())), 16).left(6), n, kind == u"child"_s ? u"child"_s : u"adult"_s};
    h.people.append(p);
    m_store.setHousehold(h);
    after();
    return p.id;
}
void GearEngine::removePerson(const QString &id)
{
    Household h = m_store.household();
    if (h.people.size() <= 1) return;   // a household always has someone
    for (int i = 0; i < h.people.size(); ++i) if (h.people[i].id == id) h.people.removeAt(i);
    m_store.setHousehold(h);
    if (m_me == id) m_me = h.people.first().id;
    after();
}
void GearEngine::setPets(int n)
{
    Household h = m_store.household();
    h.pets = qBound(0, n, 99);
    m_store.setHousehold(h);
    after();
}

QVariantList GearEngine::kits() const
{
    QVariantList out;
    const QDate t = today();
    for (const GearKit &k : m_store.kitsFor(m_me)) {
        QString sc = k.scenario;
        if (sc.isEmpty()) for (const Scenario &s : m_set.scenarios) if (s.kitType == k.type) { sc = s.id; break; }
        const Audit a = sc.isEmpty() ? Audit() : m_store.audit(m_set, k.id, sc, m_me, t);
        int expiring = 0;
        for (const GearItem &i : k.items) { const QDate e = QDate::fromString(i.expires, Qt::ISODate); if (e.isValid() && t.daysTo(e) <= 30) ++expiring; }
        out.append(QVariantMap{{u"id"_s, k.id}, {u"name"_s, k.name}, {u"type"_s, k.type}, {u"owner"_s, k.owner}, {u"mine"_s, k.owner == m_me}, {u"shared"_s, k.shared},
                               {u"scenario"_s, sc}, {u"count"_s, int(k.items.size())}, {u"weightText"_s, weightText(k.weightG())},
                               {u"score"_s, a.score}, {u"scored"_s, !a.rows.isEmpty()}, {u"missing"_s, a.missing}, {u"expiring"_s, expiring}});
    }
    return out;
}

QVariantMap GearEngine::kit() const
{
    for (const GearKit &k : m_store.kitsFor(m_me)) {
        if (k.id != m_selected) continue;
        QVariantList items;
        const QDate t = today();
        for (const GearItem &i : k.items) items.append(itemMap(i, t));
        return {{u"id"_s, k.id}, {u"name"_s, k.name}, {u"type"_s, k.type}, {u"owner"_s, k.owner}, {u"mine"_s, k.owner == m_me}, {u"shared"_s, k.shared},
                {u"scenario"_s, k.scenario}, {u"items"_s, items}, {u"weightText"_s, weightText(k.weightG())}};
    }
    return {};
}

QVariantMap GearEngine::readiness() const
{
    return m_store.readiness(m_set, m_me, today()).toVariantMap();
}
QVariantList GearEngine::reminders() const
{
    QVariantList out;
    for (const Reminder &r : m_store.reminders(m_me, today()))
        out.append(QVariantMap{{u"id"_s, r.id}, {u"kind"_s, r.kind}, {u"kitId"_s, r.kitId}, {u"itemId"_s, r.itemId}, {u"title"_s, r.title}, {u"body"_s, r.body}, {u"daysLeft"_s, r.daysLeft}});
    return out;
}
QVariantList GearEngine::scenarios() const
{
    QVariantList out;
    for (const Scenario &s : m_set.scenarios) out.append(scenario(s.id));
    return out;
}
QVariantMap GearEngine::scenario(const QString &id) const
{
    const Scenario *s = m_set.find(id);
    if (!s) return {};
    QVariantList src;
    for (const QString &sid : s->sources)
        if (const ScenarioSource *x = m_set.source(sid))
            src.append(QVariantMap{{u"id"_s, x->id}, {u"title"_s, x->title}, {u"publisher"_s, x->publisher}, {u"url"_s, x->url}, {u"terms"_s, x->terms}});
    return {{u"id"_s, s->id}, {u"title"_s, s->title}, {u"summary"_s, s->summary}, {u"kitType"_s, s->kitType}, {u"sources"_s, src}, {u"rules"_s, int(s->rules.size())}};
}
QVariantList GearEngine::categories() const
{
    QVariantList out;
    for (const QString &c : gearCategories()) out.append(c);
    return out;
}

QString GearEngine::addKit(const QString &name, const QString &type, bool shared)
{
    const QString id = m_store.addKit(name, type, m_me, shared);
    m_selected = id;
    after();
    return id;
}
QString GearEngine::createFromScenario(const QString &scenarioId, const QString &name, bool shared)
{
    const QString id = m_store.createKitFromScenario(m_set, scenarioId, name, m_me, shared);
    if (!id.isEmpty()) m_selected = id;
    after();
    return id;
}
void GearEngine::removeKit(const QString &id)
{
    const GearKit *k = m_store.kit(id);
    if (!k || k->owner != m_me) return;   // only the owner removes a kit
    m_store.removeKit(id);
    if (m_selected == id) m_selected.clear();
    after();
}
void GearEngine::selectByName(const QString &prefix)
{
    for (const GearKit &k : m_store.kitsFor(m_me))
        if (k.name.startsWith(prefix, Qt::CaseInsensitive)) { select(k.id); return; }
}
void GearEngine::setShared(const QString &id, bool shared) { m_store.setKitShared(id, shared, m_me); after(); }
void GearEngine::select(const QString &id) { m_selected = id; m_audit.clear(); m_auditScenario.clear(); Q_EMIT changed(); Q_EMIT auditChanged(); }

QString GearEngine::saveItem(const QVariantMap &m)
{
    const QString name = m.value(u"name"_s).toString().trimmed();
    if (name.isEmpty()) return tr("Give the item a name.");
    GearItem i;
    i.id = m.value(u"id"_s).toString();
    i.name = name;
    i.category = m.value(u"category"_s).toString();
    i.qty = m.value(u"qty"_s).toString().replace(u',', u'.').toDouble();
    i.unit = m.value(u"unit"_s).toString().trimmed();
    const QString exp = m.value(u"expires"_s).toString().trimmed();
    if (!exp.isEmpty() && !QDate::fromString(exp, Qt::ISODate).isValid()) return tr("Write the date as year-month-day, for example 2027-03-31.");
    i.expires = exp;
    i.rotateMonths = qBound(0, m.value(u"rotateMonths"_s).toInt(), 240);
    i.location = m.value(u"location"_s).toString().trimmed();
    i.weightG = qMax(0.0, m.value(u"weightG"_s).toString().replace(u',', u'.').toDouble());
    i.notes = m.value(u"notes"_s).toString();
    i.isPrivate = m.value(u"private"_s).toBool();
    i.key = m.value(u"key"_s).toString();
    i.lastRotated = m.value(u"lastRotated"_s).toString();
    bool ok = true;
    if (i.id.isEmpty()) ok = !m_store.addItem(m_selected, i).isEmpty(); else ok = m_store.updateItem(m_selected, i);
    if (!ok) return tr("That kit is not there any more.");
    after();
    return {};
}
void GearEngine::removeItem(const QString &itemId) { m_store.removeItem(m_selected, itemId); after(); }
void GearEngine::rotateItem(const QString &itemId) { m_store.rotate(m_selected, itemId, today()); after(); }
QString GearEngine::attachPhoto(const QString &itemId, const QString &path)
{
    QString p = path.trimmed();
    if (p.startsWith(u"file://"_s)) p = QUrl(p).toLocalFile();
    QString err;
    if (!m_store.attachPhoto(m_selected, itemId, p, &err)) return err;
    after();
    return {};
}
QString GearEngine::photoUrl(const QString &relative) const
{
    return relative.isEmpty() ? QString() : QUrl::fromLocalFile(m_dir + u"/"_s + relative).toString();
}

void GearEngine::runAudit(const QString &scenarioId)
{
    m_auditScenario = scenarioId;
    const Audit a = m_store.audit(m_set, m_selected, scenarioId, m_me, today());
    QVariantList rows;
    for (const AuditRow &r : a.rows)
        rows.append(QVariantMap{{u"key"_s, r.key}, {u"name"_s, r.name}, {u"category"_s, r.category}, {u"status"_s, r.status},
                                {u"needed"_s, qtyText(r.needed)}, {u"have"_s, qtyText(r.have)}, {u"lacking"_s, qtyText(r.lacking)}, {u"unit"_s, r.unit},
                                {u"note"_s, r.note}, {u"expiredIgnored"_s, r.expiredIgnored}, {u"source"_s, r.source}});
    m_audit = {{u"scenario"_s, scenario(scenarioId)}, {u"rows"_s, rows}, {u"score"_s, a.score}, {u"missing"_s, a.missing}, {u"short"_s, a.shortCount}, {u"ok"_s, a.ok},
               {u"people"_s, m_store.household().peopleCount()}, {u"pets"_s, m_store.household().pets}};
    Q_EMIT auditChanged();
}

QVariantList GearEngine::shopping() const
{
    QVariantList out;
    for (const ShoppingList &l : m_store.shopping()) {
        if (!m_store.kitsFor(m_me).isEmpty()) {
            bool vis = false;
            for (const GearKit &k : m_store.kitsFor(m_me)) vis |= k.id == l.kitId;
            if (!vis) continue;
        }
        QVariantList items;
        for (const ShoppingItem &i : l.items) items.append(QVariantMap{{u"name"_s, i.name}, {u"category"_s, i.category}, {u"unit"_s, i.unit}, {u"qty"_s, qtyText(i.qty)}});
        out.append(QVariantMap{{u"id"_s, l.id}, {u"kitId"_s, l.kitId}, {u"status"_s, l.status}, {u"createdBy"_s, l.createdBy}, {u"approvedBy"_s, l.approvedBy},
                               {u"scenario"_s, scenario(l.scenarioId).value(u"title"_s)}, {u"items"_s, items}, {u"agent"_s, l.createdBy.startsWith(u"agent"_s)}});
    }
    return out;
}
QString GearEngine::draftShopping(const QString &scenarioId)
{
    const QString id = m_store.proposeShopping(m_set, m_selected, scenarioId, m_me, m_me, today());
    if (id.isEmpty()) return tr("Nothing is missing, so there is nothing to buy.");
    after();
    return {};
}
QString GearEngine::approveShopping(const QString &id)
{
    QString err;
    if (!m_store.approveShopping(id, m_me, &err)) return err;
    after();
    return {};
}
void GearEngine::removeShopping(const QString &id) { m_store.removeShopping(id); after(); }

} // namespace Taos::Survival
