#pragma once
#include <QString>
#include <QVector>

#include "swaytree.h"

// Most recently used order of window ids. The front is the window focused last.
class MruTracker {
public:
    // Replaces the order with `seed`, keeping ids already known in their current relative order
    // ahead of any new ones; used for the first snapshot and to reconcile later ones.
    void reconcile(const QVector<int> &present, const QVector<int> &seedForNew);
    void focus(int id);          // moves id to the front (adds it when unknown)
    void remove(int id);
    const QVector<int> &order() const { return m_order; }
    // `windows` sorted by the MRU order; ids missing from the list go last, in input order.
    QVector<WinInfo> sorted(const QVector<WinInfo> &windows) const;
private:
    QVector<int> m_order;
};

namespace WindowFilter {
// Every whitespace separated word of `query` must occur (case insensitive) in the title, app id,
// the display name of the app or the workspace name. An empty query matches everything.
bool matches(const WinInfo &w, const QString &appName, const QString &query);
}
