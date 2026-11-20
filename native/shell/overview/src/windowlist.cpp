#include "windowlist.h"

#include <algorithm>

void MruTracker::reconcile(const QVector<int> &present, const QVector<int> &seedForNew) {
    QVector<int> next;
    for (int id : std::as_const(m_order)) if (present.contains(id)) next.push_back(id);
    // New windows (never seen focused): placed by the seed order, behind the known ones,
    // except when nothing is known yet, where the seed is the order.
    for (int id : seedForNew) if (present.contains(id) && !next.contains(id)) next.push_back(id);
    for (int id : present) if (!next.contains(id)) next.push_back(id);
    m_order = next;
}

void MruTracker::focus(int id) {
    m_order.removeAll(id);
    m_order.push_front(id);
}

void MruTracker::remove(int id) { m_order.removeAll(id); }

QVector<WinInfo> MruTracker::sorted(const QVector<WinInfo> &windows) const {
    QVector<WinInfo> out = windows;
    std::stable_sort(out.begin(), out.end(), [&](const WinInfo &a, const WinInfo &b) {
        const int ia = m_order.indexOf(a.conId), ib = m_order.indexOf(b.conId);
        return (ia < 0 ? 1 << 20 : ia) < (ib < 0 ? 1 << 20 : ib);
    });
    return out;
}

bool WindowFilter::matches(const WinInfo &w, const QString &appName, const QString &query) {
    const QStringList words = query.split(QLatin1Char(' '), Qt::SkipEmptyParts);
    if (words.isEmpty()) return true;
    const QString hay = (w.title + QLatin1Char('\n') + w.appId + QLatin1Char('\n') + appName
                         + QLatin1Char('\n') + w.workspace);
    for (const QString &word : words)
        if (!hay.contains(word, Qt::CaseInsensitive)) return false;
    return true;
}
