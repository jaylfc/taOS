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
Plan fullscreen(const Window &w);               // sway's own fullscreen
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
    bool fullscreen(const QString &ref, QString *error);
    bool close(const QString &ref, QString *error);
    // The container of this process, or 0.
    // Makes a floating window end up at `want` (a client may answer its first configure after our resize and
    // undo it): looks a few times and repeats resize and move. False when it never got there.
    bool settleGeometry(int id, const QRect &want);
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
