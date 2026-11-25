// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMap>
#include <QString>
#include <QStringList>
#include <QVector>
#include <functional>

#include "swaytree.h"

// taOS spaces: the person's desktops, kept like macOS Spaces or KDE virtual desktops.
//
// sway throws an empty workspace away the moment it is left, so the list of spaces cannot live in
// sway. This file keeps it: an ordered list per output, saved in the person's own config, is the
// source of truth for the strip. A space only has a sway workspace while it holds a window or is
// being looked at; entering an empty one simply creates the workspace again. Nothing is placed in
// sway to hold a space open (no placeholder windows).
//
// The sway workspace name of a space is its `key` and never changes, so mode settings and the
// marks of minimised windows (both keyed by workspace name) keep working across a rename or a
// reorder. What the person sees is the label: their own name, else "Desktop <position>".
namespace Spaces {

struct Space {
    QString key;     // the sway workspace name
    QString name;    // the person's name for it; empty means "Desktop <position>"
};

QString defaultLabel(int position);               // 1-based
QString cleanName(const QString &raw);            // trimmed, one line, at most 40 characters

class Store {
public:
    // The file lives under the person's config directory unless TAOS_SPACES_FILE says otherwise.
    static QString defaultPath();
    explicit Store(QString path = defaultPath()) : m_path(std::move(path)) {}
    // A missing file is fine (empty store). A damaged one is moved aside (never overwritten or
    // dropped) and reported through `error`; the store starts empty and load returns false.
    bool load(QString *error);
    // Atomic (write a temp file, then rename). False with `error` when the write failed.
    bool save(QString *error) const;

    QVector<Space> spaces(const QString &output) const { return m_lists.value(output); }
    QStringList outputs() const { return m_lists.keys(); }
    bool has(const QString &key) const;
    QString outputOf(const QString &key) const;
    int positionOf(const QString &output, const QString &key) const;   // 0-based, -1 unknown
    QString label(const QString &output, const QString &key) const;
    QString current(const QString &output) const { return m_current.value(output); }
    void setCurrent(const QString &output, const QString &key) { m_current.insert(output, key); }

    // Mutations. They change memory only; the caller saves. Each returns an empty string on
    // success, else the reason.
    QString add(const QString &output, const QString &key, const QString &name = {});
    QString rename(const QString &key, const QString &name);
    QString move(const QString &key, int toPosition);                   // 0-based target
    QString remove(const QString &key);
    void relocate(const QString &key, const QString &output);   // the workspace now lives on another output
    // The smallest positive whole number not used by any space or in `alsoUsed`.
    QString freshKey(const QStringList &alsoUsed) const;

private:
    QString m_path;
    QMap<QString, QVector<Space>> m_lists;
    QMap<QString, QString> m_current;
};

// Where the engine sends sway commands (the daemon's IPC connection; a recorder in tests).
class Sink {
public:
    virtual ~Sink() = default;
    virtual void command(const QString &cmd) = 0;
};

struct View {
    QString key;
    QString label;
    int position = 0;        // 1-based
    bool custom = false;     // has the person's own name
    bool current = false;
    bool live = false;       // a sway workspace exists right now
    int windows = 0;
    bool urgent = false;
};

// The space verbs. One engine for the daemon, the command socket (`taos-overview space ...`) and the
// overview's buttons, so a person and an agent do the same thing.
class Engine {
public:
    Engine(Store *store, Sink *sink) : m_store(store), m_sink(sink) {}
    // Takes a fresh sway snapshot: adopts workspaces that hold windows and are not yet known, never
    // forgets a known space because sway has dropped its empty workspace, remembers the current space,
    // and on the first call after start puts the person back on their saved space when sway came up
    // on a bare workspace. `output` is the output the verbs act on (the focused one).
    void sync(const Snapshot &s, const QString &output);
    QVector<View> views() const;                 // the strip, in order, for the current output
    const QString &output() const { return m_output; }
    Store *store() { return m_store; }

    QString add(const QString &name, QString *newKey = nullptr);   // errors as text, "" on success
    QString rename(const QString &key, const QString &name);
    QString move(const QString &key, int toPosition);
    QString remove(const QString &key);
    QString go(const QString &key);
    QString moveWindow(int conId, const QString &key);
    QString lastError() const { return m_lastError; }

    // `space list|add|rename|move|remove|go|move-window ...`; returns the reply line.
    QString handle(const QStringList &words);

    static QString wsRef(const QString &key);    // `number 3` or `"web"` for sway commands

private:
    QString save();
    QString keyFor(const QString &positionOrKey) const;
    Store *m_store;
    Sink *m_sink;
    Snapshot m_snap;
    QString m_output;
    bool m_started = false;
    QString m_lastError;
};

}  // namespace Spaces
