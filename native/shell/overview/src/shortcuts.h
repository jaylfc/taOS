// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QRegularExpression>
#include <QString>
#include <QStringList>
#include <QVariantList>
#include <QVector>

// The keyboard shortcuts overlay's engine. The list is read from the bindings sway really loaded (the main
// config and every file it includes), never typed by hand; a registry of descriptions (shortcuts.json) turns a
// binding's command into words. A binding without a description is still shown, in its raw form, and the
// drift test (tests/test_shortcuts.cpp) fails for every binding the shell itself ships without one. The idea of
// a keybindings help generated from the live bindings is inspired by Omarchy's (MIT); nothing is copied.
namespace Shortcuts {

struct Binding {
    QString combo;        // as written, variables expanded: "Mod4+Shift+t"
    QString command;      // "exec taos-window mode toggle"
    bool release = false; // --release: fires when the key goes up (a tap)
    bool locked = false;
    QString file;         // where it came from
};

struct ParseResult {
    QVector<Binding> bindings;      // default mode only, the last definition of a combo wins
    QString floatingModifier;       // "Mod4" when `floating_modifier Mod4 ...` is set
    QStringList errors;             // unreadable includes and the like
};
// Reads `mainPath` and follows `include` lines (globs, ~, relative to the including file), expands `set $var`.
ParseResult parseConfig(const QString &mainPath);

QString prettyKey(const QString &token);                       // "Mod4" -> "Super", "Return" -> "Enter"
QStringList prettyKeys(const QString &combo, bool release);    // "Mod4+Shift+t" -> Super, Shift, T

struct Gesture {
    QStringList keys;
    QString text, group;
    bool touch = false;   // shown on the phone version
    bool pointer = true;  // shown with a keyboard and mouse
};

class Registry {
public:
    struct Entry {
        QRegularExpression match;   // the whole command
        QString group, text;        // text may use %1, %2 for captures
        QString merge;              // when set, bindings of this entry become one row ("1 to 9", "arrows")
    };
    bool load(const QByteArray &json, QString *error);
    const Entry *find(const QString &command) const;
    QStringList groupOrder;
    QVector<Gesture> gestures;
    QString otherGroup = QStringLiteral("Other shortcuts");
private:
    QVector<Entry> m_entries;
};

struct Row {
    QStringList keys;
    QString text;          // the description, or the raw command when none exists
    bool described = true;
    bool gesture = false;
};
struct Group {
    QString title;
    QVector<Row> rows;
};

// Groups in the registry's order; undescribed bindings land in the "other" group with their raw command.
QVector<Group> build(const ParseResult &p, const Registry &r, bool touch = false);
// Apps may declare their own: <dir>/<app_id>.json = {"title": "...", "shortcuts": [{"keys": ["Ctrl","S"], "text": "Save"}]}
Group appGroup(const QString &appId, const QString &appName, const QStringList &dirs);
// Case insensitive; every word of `query` must be in the text, the keys or the group title.
QVector<Group> filter(const QVector<Group> &g, const QString &query);
QVariantList toVariant(const QVector<Group> &g);
}  // namespace Shortcuts
