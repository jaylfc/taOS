// SPDX-License-Identifier: AGPL-3.0-or-later
#include "appinfo.h"

AppInfo::AppInfo() {}

std::shared_ptr<DesktopIndex> AppInfo::scan(const QStringList &dirs) {
    auto idx = std::make_shared<DesktopIndex>();
    idx->scan(dirs);
    return idx;
}

void AppInfo::startScan(const QStringList &dirs) {
    m_future = std::async(std::launch::async, [dirs] { return AppInfo::scan(dirs); }).share();
    m_ready = false;
}

void AppInfo::setEntries(const QVector<DesktopEntry> &entries) {
    m_index = std::make_shared<DesktopIndex>();
    for (const DesktopEntry &e : entries) m_index->add(e);
    m_ready = true;
}

void AppInfo::ensure() const {
    if (m_ready) return;
    if (m_future.valid()) m_index = m_future.get();
    m_ready = true;
}

AppInfo::Entry AppInfo::lookup(const QString &appId) const {
    ensure();
    if (!m_index) return {};
    const DesktopEntry *e = m_index->matchWindow(appId);
    return e ? Entry { e->name, e->icon } : Entry {};
}

QString AppInfo::displayName(const QString &appId) const {
    const Entry e = lookup(appId);
    if (!e.name.isEmpty()) return e.name;
    if (appId.isEmpty()) return QStringLiteral("Window");
    QString n = appId.section(QLatin1Char('.'), -1);
    if (n.isEmpty()) n = appId;
    n[0] = n[0].toUpper();
    return n;
}
