// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonObject>
#include <QString>
#include <functional>

#include "types.h"

namespace taos::search {

// The system action menu: a short hierarchy (Capture, Toggle, Style, Setup, Install, System) of things the person can
// do, each backed by an existing engine or CLI and offered only when that exists. Same shape as the app menu for the
// faces: a level is a short list, filtering happens per level, Backspace goes one level up.
// The idea of a keyboard first, hierarchical system menu with a type to filter list at each level comes from the
// Omarchy menu (github.com/basecamp/omarchy, MIT, DHH); no code or text is taken from it.
struct ActionNode {
    QString id;                 // "toggle" or "toggle/wifi-off"
    QString title, subtitle, icon;
    QStringList keywords;
    bool isGroup = false;       // opens a level
    int childCount = 0;
    Action action;              // leaves only
};

class ActionMenu {
public:
    // `exists` answers "is this executable on PATH" (injected by tests)
    explicit ActionMenu(std::function<bool(const QString &)> exists = {});
    QList<ActionNode> level(const QStringList &path) const;                          // path = group ids from the top
    QList<ActionNode> filter(const QStringList &path, const QString &text) const;    // fuzzy, within one level
    ActionNode find(const QString &id) const;                                         // by full id; empty id when unknown
    QJsonObject toJson() const;
    static QString idOf(const QStringList &path) { return path.join(QLatin1Char('/')); }
private:
    struct Impl;
    std::shared_ptr<Impl> d;
};

}  // namespace taos::search
