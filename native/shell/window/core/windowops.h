// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QString>
#include <QStringList>

#include "windowmodel.h"

namespace TaosWin {

// What the controller needs from sway. SwayTransport talks to the real compositor; the tests use a fake
// that records the commands.
class Transport {
public:
    virtual ~Transport() = default;
    virtual bool getTree(QByteArray *json, QString *error) = 0;
    // One sway command line (it may hold several commands separated by ";").
    virtual bool runCommand(const QString &cmd, QString *error) = 0;
};

class SwayTransport : public Transport {
public:
    explicit SwayTransport(const QString &socketPath = QString()) : m_path(socketPath) {}
    ~SwayTransport() override;
    bool getTree(QByteArray *json, QString *error) override;
    bool runCommand(const QString &cmd, QString *error) override;
    bool connectNow(QString *error);

private:
    struct Impl;
    QString m_path;
    Impl *m_impl = nullptr;
};

// The sway commands for each action, as pure functions of a window and the tree they came from.
// The first string of an Error result is a plain sentence for the person.
struct Plan {
    QStringList commands;
    QString error;      // set: do nothing, report this
    int settleId = 0;   // a floating window that must end up at `settle` (a client may undo the first resize)
    QRect settle;
    QString settleHidden;   // ... and, when its workspace is not shown, the name to send it back to after each move
    bool ok() const { return error.isEmpty(); }
};
namespace Plans {
QString quoted(const QString &s);               // "..." for a sway command argument
QString sel(int id);                            // [con_id=N]
Plan minimise(const Window &w, qint64 nowMs);
Plan restore(const Window &w, const Tree &t);
// A window another client minimised (xdg_toplevel.set_minimized with swayfx's scratchpad_minimize, a foreign-toplevel
// client such as a panel) was parked without our marks. Given the window as it was just before (`before`) and as
// it is now, the marks that make it an ordinary taos-minimised window. Empty when there is nothing to adopt.
Plan adoptMinimised(const Window &before, const Window &now, qint64 nowMs);
// A window that came back on a workspace by other means: drop its stale minimise marks.
Plan clearMinimiseMarks(const Window &w);
Plan full(const Window &w, const Tree &t);      // toggle full size
// Full size, and only that: a window that already has it gets the usable area again, one in sway's fullscreen leaves
// it first. Never goes back (that is `full`, a toggle, or `unmaximise`).
Plan maximise(const Window &w, const Tree &t);
// What `restore` does to a window that is on screen: leave sway's fullscreen, then give up full size and go back to
// the geometry (or the tiling) it had. Nothing to undo: no commands.
Plan unmaximise(const Window &w, const Tree &t);
enum class Fullscreen { Toggle, On, Off };
Plan fullscreen(const Window &w, Fullscreen mode = Fullscreen::Toggle);   // sway's own fullscreen (the client gets set_fullscreen)
// Onto the focused workspace (the screen the person is looking at), a floating window centred at a size that fits.
Plan bringHere(const Window &w, const Tree &t);
// A floating window back to the default size for its screen, centred on its own workspace.
Plan resetSize(const Window &w, const Tree &t);
// To the workspace `space` (made when it does not exist yet). Empty: the focused workspace.
Plan moveToSpace(const Window &w, const Tree &t, const QString &space);
// The name of a new, empty space: the next number after the highest in use (as the overview names it).
QString newSpaceName(const Tree &t);
// Where a floating window lands on a workspace area: full size fills it, else its own size when sensible, centred.
QRect landingRect(const Window &w, const QRect &area);
Plan close(const Window &w);
Plan focus(const Window &w, const Tree &t);     // restores first when minimised
}  // namespace Plans

// The engine: resolves an id (a number, "focused", "last"), plans, runs.
class WindowController {
public:
    explicit WindowController(Transport *t) : m_t(t) {}
    bool list(Tree *out, QString *error);
    // `ref`: a container id, "focused", or (restore and focus only) "last" = the most recently minimised.
    bool minimise(const QString &ref, QString *error);
    bool restore(const QString &ref, QString *error);
    bool toggleMinimise(const QString &ref, QString *error);
    bool focus(const QString &ref, QString *error);
    bool full(const QString &ref, QString *error);
    // `mode` On/Off are idempotent; Toggle is the key binding.
    bool fullscreen(const QString &ref, QString *error, Plans::Fullscreen mode = Plans::Fullscreen::Toggle);
    bool maximise(const QString &ref, QString *error);
    bool bringHere(const QString &ref, QString *error);
    bool resetSize(const QString &ref, QString *error);
    bool moveToSpace(const QString &ref, const QString &space, QString *error);
    // A new empty space; with a window ref, that window goes there and the space is shown. `created` gets its name.
    bool newSpace(const QString &ref, QString *created, QString *error);
    bool close(const QString &ref, QString *error);
    // The container of this process, or 0.
    // Makes a floating window end up at `want` (a client may answer its first configure after our resize and
    // undo it): looks a few times and repeats resize and move. False when it never got there.
    bool settleGeometry(int id, const QRect &want, const QString &hiddenWorkspace = QString());
    // The same for "fill the usable area": a few px of border difference are asked for as well.
    void settleFull(int id, const QRect &area, const QString &hiddenWorkspace = QString());
    // One raw sway command (used by the mode switch).
    bool runRaw(const QString &cmd, QString *error) { return m_t->runCommand(cmd, error); }
    int ownWindowId(int pid, const QString &appId, QString *error = nullptr);
    void setClock(qint64 (*now)()) { m_now = now; }

private:
    bool resolve(const QString &ref, Tree *tree, const Window **w, QString *error, bool allowLast);
    bool run(const Plan &p, QString *error);
    Transport *m_t;
    qint64 (*m_now)() = nullptr;
};

}  // namespace TaosWin
