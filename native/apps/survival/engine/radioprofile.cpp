// SPDX-License-Identifier: AGPL-3.0-or-later
#include "radioprofile.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

using namespace Qt::Literals::StringLiterals;

namespace Taos::Survival {

RadioProfiles::RadioProfiles(QString dataDir) : m_dir(std::move(dataDir)) {}

QVariantMap RadioProfiles::readAll() const
{
    QFile f(path());
    if (!f.open(QIODevice::ReadOnly))
        return {};
    return QJsonDocument::fromJson(f.readAll()).object().value(u"profiles"_s).toObject().toVariantMap();
}

bool RadioProfiles::save(const QString &deviceId, const QString &network, const QVariantMap &values, const QString &hardware, const QString &firmware, QString *error)
{
    if (deviceId.isEmpty() || network.isEmpty() || values.isEmpty()) {
        if (error)
            *error = QObject::tr("There is nothing to save for this radio.");
        return false;
    }
    QVariantMap all = readAll();
    QVariantMap board = all.value(deviceId).toMap();
    QVariantMap kept = values;
    kept.remove(u"options"_s);   // the engine's own option lists, not the person's settings
    board.insert(network, QVariantMap{{u"savedMs"_s, double(QDateTime::currentMSecsSinceEpoch())}, {u"hardware"_s, hardware}, {u"firmware"_s, firmware}, {u"values"_s, kept}});
    all.insert(deviceId, board);
    if (!QDir().mkpath(m_dir)) {
        if (error)
            *error = QObject::tr("The folder for saved radio settings cannot be made.");
        return false;
    }
    QSaveFile f(path());
    if (!f.open(QIODevice::WriteOnly)) {
        if (error)
            *error = QObject::tr("The saved radio settings cannot be written: %1").arg(f.errorString());
        return false;
    }
    f.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    f.write(QJsonDocument(QJsonObject{{u"version"_s, 1}, {u"profiles"_s, QJsonObject::fromVariantMap(all)}}).toJson(QJsonDocument::Indented));
    if (!f.commit()) {
        if (error)
            *error = QObject::tr("The saved radio settings cannot be written: %1").arg(f.errorString());
        return false;
    }
    return true;
}

bool RadioProfiles::has(const QString &deviceId, const QString &network) const { return !load(deviceId, network).isEmpty(); }

QVariantMap RadioProfiles::load(const QString &deviceId, const QString &network) const
{
    return readAll().value(deviceId).toMap().value(network).toMap();
}

QVariantList RadioProfiles::list(const QString &deviceId) const
{
    QVariantList out;
    const QVariantMap board = readAll().value(deviceId).toMap();
    for (auto it = board.cbegin(); it != board.cend(); ++it) {
        const QVariantMap p = it.value().toMap();
        out.append(QVariantMap{{u"network"_s, it.key()}, {u"savedMs"_s, p.value(u"savedMs"_s)}, {u"hardware"_s, p.value(u"hardware"_s)}, {u"firmware"_s, p.value(u"firmware"_s)}});
    }
    std::sort(out.begin(), out.end(), [](const QVariant &a, const QVariant &b) { return a.toMap().value(u"savedMs"_s).toDouble() < b.toMap().value(u"savedMs"_s).toDouble(); });
    return out;
}

} // namespace Taos::Survival
