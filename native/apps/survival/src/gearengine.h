// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The face of the gear manager for QML. All logic is in engine/gear.*; this keeps the selection, saves after every
// change and turns the data into QML values.
#include <QObject>
#include <QVariant>

#include "gear.h"

namespace Taos::Survival {

class GearEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantList people READ people NOTIFY changed)
    Q_PROPERTY(int pets READ pets NOTIFY changed)
    Q_PROPERTY(QString me READ me NOTIFY changed)
    Q_PROPERTY(QVariantList kits READ kits NOTIFY changed)
    Q_PROPERTY(QString selected READ selected NOTIFY changed)
    Q_PROPERTY(QVariantMap kit READ kit NOTIFY changed)
    Q_PROPERTY(QVariantMap readiness READ readiness NOTIFY changed)
    Q_PROPERTY(QVariantList reminders READ reminders NOTIFY changed)
    Q_PROPERTY(QVariantList scenarios READ scenarios CONSTANT)
    Q_PROPERTY(QVariantList categories READ categories CONSTANT)
    Q_PROPERTY(QVariantMap audit READ audit NOTIFY auditChanged)
    Q_PROPERTY(QVariantList shopping READ shopping NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(QString tab READ tab WRITE setTab NOTIFY tabChanged)
public:
    explicit GearEngine(const QString &dataDir, QObject *parent = nullptr);
    QVariantList people() const;
    int pets() const { return m_store.household().pets; }
    QString me() const { return m_me; }
    QVariantList kits() const;
    QString selected() const { return m_selected; }
    QVariantMap kit() const;
    QVariantMap readiness() const;
    QVariantList reminders() const;
    QVariantList scenarios() const;
    QVariantList categories() const;
    QVariantMap audit() const { return m_audit; }
    QVariantList shopping() const;
    QString error() const { return m_error; }
    QDate today() const;
    QString tab() const { return m_tab; }
    void setTab(const QString &t) { m_tab = t; Q_EMIT tabChanged(); }
    /// Selects the first kit whose name starts with `prefix` (for links and screenshots).
    Q_INVOKABLE void selectByName(const QString &prefix);

    Q_INVOKABLE void setMe(const QString &personId);
    Q_INVOKABLE QString addPerson(const QString &name, const QString &kind);
    Q_INVOKABLE void removePerson(const QString &id);
    Q_INVOKABLE void setPets(int n);
    Q_INVOKABLE QString addKit(const QString &name, const QString &type, bool shared);
    Q_INVOKABLE QString createFromScenario(const QString &scenarioId, const QString &name, bool shared);
    Q_INVOKABLE void removeKit(const QString &id);
    Q_INVOKABLE void setShared(const QString &id, bool shared);
    Q_INVOKABLE void select(const QString &id);
    /// A map with name, category, qty, unit, expires, rotateMonths, location, weightG, notes, private, id (to edit). Returns an error in words or empty.
    Q_INVOKABLE QString saveItem(const QVariantMap &item);
    Q_INVOKABLE void removeItem(const QString &itemId);
    Q_INVOKABLE void rotateItem(const QString &itemId);
    Q_INVOKABLE QString attachPhoto(const QString &itemId, const QString &path);
    Q_INVOKABLE void runAudit(const QString &scenarioId);
    Q_INVOKABLE QString draftShopping(const QString &scenarioId);
    Q_INVOKABLE QString approveShopping(const QString &id);
    Q_INVOKABLE void removeShopping(const QString &id);
    Q_INVOKABLE QVariantMap scenario(const QString &id) const;
    Q_INVOKABLE QString photoUrl(const QString &relative) const;
Q_SIGNALS:
    void changed();
    void auditChanged();
    void tabChanged();
private:
    void persist();
    void after();
    QString m_tab = QStringLiteral("items");
    QString m_dir, m_me, m_selected, m_error, m_auditScenario;
    GearStore m_store;
    ScenarioSet m_set;
    QVariantMap m_audit;
};

} // namespace Taos::Survival
