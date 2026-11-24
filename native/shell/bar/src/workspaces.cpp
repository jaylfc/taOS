// SPDX-License-Identifier: AGPL-3.0-or-later
#include "workspaces.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <algorithm>

#include "swayrunner.h"

Workspaces::Workspaces(SwayRunner *runner, QObject *parent) : QObject(parent), m_runner(runner) {
    connect(&m_timer, &QTimer::timeout, this, &Workspaces::refresh);
}

void Workspaces::start(int ms) {
    m_timer.start(ms);
    refresh();
}

QVariantList Workspaces::parse(const QByteArray &json) {
    QVector<QPair<int, QVariantMap>> v;
    for (const QJsonValue &x : QJsonDocument::fromJson(json).array()) {
        const QJsonObject o = x.toObject();
        QVariantMap m{{QStringLiteral("name"), o.value(QStringLiteral("name")).toString()},
                      {QStringLiteral("focused"), o.value(QStringLiteral("focused")).toBool()}};
        v.append({o.value(QStringLiteral("num")).toInt(1 << 20), m});
    }
    std::stable_sort(v.begin(), v.end(), [](const auto &a, const auto &b) { return a.first < b.first; });
    QVariantList out;
    for (const auto &p : v) out << p.second;
    return out;
}

void Workspaces::refresh() {
    if (!m_runner) return;
    const SwayRunner::Result r = m_runner->run({QStringLiteral("-t"), QStringLiteral("get_workspaces")});
    if (!r.ok) return;
    const QVariantList next = parse(r.out);
    if (next == m_list) return;
    m_list = next;
    emit changed();
}
