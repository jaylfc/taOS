// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QVariantList>

namespace TaosSettings {

// What this device is, read from the system (os-release, /proc, the file system). `root` is a prefix for
// /etc and /proc, empty on a real system. The taOS version comes from the controller (GET /api/version) and is
// shown by the page.
class AboutInfo : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString deviceName READ deviceName NOTIFY changed)
    Q_PROPERTY(QString osName READ osName NOTIFY changed)
    Q_PROPERTY(QVariantList rows READ rows NOTIFY changed)   // [{label, value}]
public:
    explicit AboutInfo(const QString &root = QString(), const QString &storagePath = QString(), QObject *parent = nullptr);
    QString deviceName() const { return m_device; }
    QString osName() const { return m_os; }
    QVariantList rows() const { return m_rows; }
    Q_INVOKABLE void refresh();
    // Opens Activity (the live system view).
    Q_INVOKABLE bool openActivity() const;
    static QString formatBytes(qint64 bytes);
signals:
    void changed();
private:
    QString m_root, m_storage, m_device, m_os;
    QVariantList m_rows;
};

} // namespace TaosSettings
