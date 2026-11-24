// SPDX-License-Identifier: AGPL-3.0-or-later
#include "frecency.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QStandardPaths>
#include <algorithm>
#include <cmath>

namespace taos::search {

static constexpr double kHalfLifeSecs = 14.0 * 24 * 3600;

QString Frecency::defaultPath() {
    QString base = qEnvironmentVariable("XDG_STATE_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.local/state");
    return base + QStringLiteral("/taos/launcher.json");
}

Frecency::Frecency(const QString &path) : m_path(path) { load(); }

double Frecency::decayed(const Entry &e, qint64 now) {
    const double dt = qMax<qint64>(0, now - e.at);
    return e.score * std::pow(0.5, dt / kHalfLifeSecs);
}

void Frecency::load() {
    QFile f(m_path);
    if (!f.open(QIODevice::ReadOnly)) return;
    const QJsonDocument d = QJsonDocument::fromJson(f.read(4 * 1024 * 1024));
    if (!d.isObject()) return;   // corrupt file: start empty, the next save replaces it
    const QJsonObject items = d.object().value(QStringLiteral("items")).toObject();
    for (auto it = items.begin(); it != items.end(); ++it) {
        const QJsonObject o = it.value().toObject();
        Entry e;
        e.score = o.value(QStringLiteral("score")).toDouble();
        e.at = qint64(o.value(QStringLiteral("at")).toDouble());
        if (e.score > 0 && e.at > 0) m_e.insert(it.key(), e);
    }
}

void Frecency::record(const QString &id, qint64 now) {
    if (id.isEmpty()) return;
    if (now <= 0) now = QDateTime::currentSecsSinceEpoch();
    {
        QMutexLocker l(&m_mu);
        Entry &e = m_e[id];
        e.score = decayed(e, now) + 1.0;
        e.at = now;
    }
    save();
}

double Frecency::boost(const QString &id, qint64 now) const {
    if (now <= 0) now = QDateTime::currentSecsSinceEpoch();
    QMutexLocker l(&m_mu);
    auto it = m_e.constFind(id);
    if (it == m_e.constEnd()) return 0;
    return qMin(1.0, std::log2(1.0 + decayed(*it, now)) / 4.0);
}

QStringList Frecency::top(const QString &prefix, int n, qint64 now) const {
    if (now <= 0) now = QDateTime::currentSecsSinceEpoch();
    QMutexLocker l(&m_mu);
    QList<QPair<double, QString>> v;
    for (auto it = m_e.cbegin(); it != m_e.cend(); ++it)
        if (it.key().startsWith(prefix)) {
            const double s = decayed(it.value(), now);
            if (s > 0.05) v.append({s, it.key()});
        }
    std::sort(v.begin(), v.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    QStringList out;
    for (int i = 0; i < v.size() && i < n; ++i) out << v[i].second;
    return out;
}

void Frecency::save() const {
    QJsonObject items;
    {
        QMutexLocker l(&m_mu);
        const qint64 now = QDateTime::currentSecsSinceEpoch();
        for (auto it = m_e.cbegin(); it != m_e.cend(); ++it) {
            if (decayed(it.value(), now) < 0.02) continue;   // forgotten
            items.insert(it.key(), QJsonObject{{QStringLiteral("score"), it.value().score}, {QStringLiteral("at"), double(it.value().at)}});
        }
    }
    QDir().mkpath(QFileInfo(m_path).absolutePath());
    QSaveFile f(m_path);
    if (!f.open(QIODevice::WriteOnly)) return;
    f.write(QJsonDocument(QJsonObject{{QStringLiteral("version"), 1}, {QStringLiteral("items"), items}}).toJson(QJsonDocument::Compact));
    f.commit();
}

}  // namespace taos::search
