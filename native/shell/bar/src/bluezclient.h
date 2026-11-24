// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>
#include <QDBusAbstractAdaptor>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QDBusServiceWatcher>
#include <QHash>
#include <QSortFilterProxyModel>
#include <QTimer>

#include "dbusutil.h"

struct BtEntry {
    QString path, name, address, icon;
    bool hasName = false, paired = false, connected = false, trusted = false, busy = false;
    int battery = -1;   // percent, -1 when the device does not report one
    int rssi = -127;
};

class BtModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { PathRole = Qt::UserRole + 1, NameRole, PairedRole, ConnectedRole, BatteryRole, BusyRole, RssiRole, DescriptionRole, IconRole };
    explicit BtModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    int find(const QString &path) const;
    const BtEntry *entry(const QString &path) const;
    void upsert(const BtEntry &e);
    void remove(const QString &path);
    void clear();
    static QString describe(const BtEntry &e);
private:
    QList<BtEntry> m_rows;
};

// Paired devices (connected first, then by name) or nearby ones (by signal).
class BtFilterModel : public QSortFilterProxyModel {
    Q_OBJECT
public:
    BtFilterModel(bool paired, QObject *parent = nullptr) : QSortFilterProxyModel(parent), m_paired(paired) {
        setDynamicSortFilter(true);
    }
protected:
    bool filterAcceptsRow(int row, const QModelIndex &parent) const override;
    bool lessThan(const QModelIndex &a, const QModelIndex &b) const override;
private:
    bool m_paired;
};

// org.bluez.Agent1: BlueZ calls this when a pairing needs the user. Requests
// are held and answered when the person decides in the menu.
class BluezAgent : public QObject {
    Q_OBJECT
public:
    enum Kind { None, Confirm, DisplayPasskey, DisplayPin, Pin, Passkey, Authorize };
    explicit BluezAgent(const QDBusConnection &bus, QObject *parent = nullptr);
    void request(Kind k, const QString &device, const QString &code, const QDBusMessage &msg);
    void display(Kind k, const QString &device, const QString &code);
    void respond(bool accept, const QString &value);
    void cancel();
    bool pending() const { return m_pending; }
signals:
    void requested(int kind, const QString &devicePath, const QString &code);
    void cancelled();
private:
    bool m_pending = false;
    Kind m_kind = None;
    QDBusMessage m_msg;
    QDBusConnection m_bus;
};

class BluezAgentAdaptor : public QDBusAbstractAdaptor {
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.bluez.Agent1")
public:
    explicit BluezAgentAdaptor(BluezAgent *a) : QDBusAbstractAdaptor(a), m_a(a) {}
public slots:
    void Release() { m_a->cancel(); }
    QString RequestPinCode(const QDBusObjectPath &d, const QDBusMessage &m);
    void DisplayPinCode(const QDBusObjectPath &d, const QString &pin);
    uint RequestPasskey(const QDBusObjectPath &d, const QDBusMessage &m);
    void DisplayPasskey(const QDBusObjectPath &d, uint passkey, ushort entered);
    void RequestConfirmation(const QDBusObjectPath &d, uint passkey, const QDBusMessage &m);
    void RequestAuthorization(const QDBusObjectPath &d, const QDBusMessage &m);
    void AuthorizeService(const QDBusObjectPath &d, const QString &uuid, const QDBusMessage &m);
    void Cancel() { m_a->cancel(); }
private:
    BluezAgent *m_a;
};

// BlueZ over D-Bus: power, discovery, paired and nearby devices, pair,
// connect, disconnect, forget. Signal driven. Link keys never pass through
// here: BlueZ keeps them.
class BluezClient : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(bool switchVisible READ switchVisible NOTIFY changed)
    Q_PROPERTY(bool powered READ powered NOTIFY changed)
    Q_PROPERTY(bool discovering READ discovering NOTIFY changed)
    Q_PROPERTY(QString message READ message NOTIFY changed)
    Q_PROPERTY(QAbstractItemModel *paired READ paired CONSTANT)
    Q_PROPERTY(QAbstractItemModel *nearby READ nearby CONSTANT)
    Q_PROPERTY(int pairedCount READ pairedCount NOTIFY countsChanged)
    Q_PROPERTY(int nearbyCount READ nearbyCount NOTIFY countsChanged)
    Q_PROPERTY(int requestKind READ requestKind NOTIFY requestChanged)
    Q_PROPERTY(QString requestDevice READ requestDevice NOTIFY requestChanged)
    Q_PROPERTY(QString requestCode READ requestCode NOTIFY requestChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
public:
    explicit BluezClient(const QDBusConnection &bus, QObject *parent = nullptr);
    void start();
    bool available() const { return m_available; }
    bool adapterPresent() const { return !m_adapter.isEmpty(); }
    bool switchVisible() const { return m_available && adapterPresent(); }
    bool powered() const { return m_powered; }
    bool discovering() const { return m_discovering; }
    QString message() const;
    QAbstractItemModel *paired() { return &m_pairedModel; }
    QAbstractItemModel *nearby() { return &m_nearbyModel; }
    BtModel *devices() { return &m_model; }
    int pairedCount() const { return m_pairedModel.rowCount(); }
    int nearbyCount() const { return m_nearbyModel.rowCount(); }
    int requestKind() const { return m_reqKind; }
    QString requestDevice() const { return m_reqDevice; }
    QString requestCode() const { return m_reqCode; }
    QString errorText() const { return m_error; }
    BluezAgent *agent() { return &m_agent; }

    Q_INVOKABLE void setActive(bool on);
    Q_INVOKABLE void setPowered(bool on);
    Q_INVOKABLE void pair(const QString &path);
    Q_INVOKABLE void connectDevice(const QString &path);
    Q_INVOKABLE void disconnectDevice(const QString &path);
    Q_INVOKABLE void forget(const QString &path);
    Q_INVOKABLE void cancelPairing();
    Q_INVOKABLE void respond(bool accept, const QString &value = QString());
    Q_INVOKABLE void clearError();

signals:
    void changed();
    void requestChanged();
    void countsChanged();
    void errorChanged();
    void announcement(const QString &text);
    void attention();  // a request arrived while the menu is closed

private slots:
    void onProps(const QDBusMessage &m);
    void onAdded(const QDBusMessage &m);
    void onRemoved(const QDBusMessage &m);

private:
    void refreshAll();
    void clearAll();
    void applyInterfaces(const QString &path, const DBusSettings &ifaces);
    void startDiscovery();
    void stopDiscovery();
    void registerAgent();
    void unregisterAgent();
    void onAgentRequest(int kind, const QString &dev, const QString &code);
    void setError(const QString &t);
    QString nameOf(const QString &path) const;
    static QString errorWords(const QString &errName, const QString &dev);

    QDBusConnection m_bus;
    QString m_service = QStringLiteral("org.bluez");
    QDBusServiceWatcher m_watcher;
    bool m_available = false, m_powered = false, m_discovering = false, m_active = false, m_agentRegistered = false;
    QString m_adapter;
    BtModel m_model;
    BtFilterModel m_pairedModel{true}, m_nearbyModel{false};
    BluezAgent m_agent;
    QString m_agentPath = QStringLiteral("/org/taos/bar/Agent");
    int m_reqKind = 0;
    QString m_reqDevice, m_reqCode, m_error;
};
