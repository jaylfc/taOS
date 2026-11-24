// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-appgroups engine: app groups (one Incus container + one grant set each) and the desktop apps installed into
// them. A plain library with no UI; the CLI, the Store and an agent all call this. See README.md.
#pragma once
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <functional>

#include "manifest.h"

namespace TaosAppGroups {

// Exit codes (the CLI returns Result::exit).
enum Exit {
    ExitOk = 0,
    ExitFailed = 1,
    ExitUsage = 2,
    ExitNotFound = 3,
    ExitIncus = 4,     // incus missing, not reachable or not allowed for this person
    ExitRefused = 5,   // manifest invalid, architecture not supported, policy
    ExitStep = 6,      // an install step failed (the log path is in the error)
    ExitConflict = 7,  // already exists or in use
    ExitNoSession = 8, // run: no desktop session to show the window in
    ExitUnavailable = 9 // the chosen install target exists in the design but is not built yet
};

struct Result {
    bool ok = true;
    int exit = ExitOk;
    QString error;
    QJsonObject data;
    static Result fail(int code, const QString &msg) { Result r; r.ok = false; r.exit = code; r.error = msg; return r; }
};

using Progress = std::function<void(const QJsonObject &)>;  // {event: step|detail, n, of, text}

constexpr const char *kDefaultGroup = "default";
constexpr const char *kDefaultContainer = "user-apps";
constexpr const char *kProfile = "user-apps";
constexpr const char *kDefaultDisplayName = "My apps";

class Engine {
public:
    Engine();
    Result ensureDefault(const Progress &p = {});
    Result groups();
    Result groupCreate(const QString &name, const Progress &p = {});
    Result install(const QString &appId, const QString &group, const Progress &p = {});
    Result remove(const QString &appId, bool purgeData, const Progress &p = {});
    Result list(bool available);
    Result catalog();
    Result status();
    // Rewrites the launcher entry and icon of every installed app (after a binary moved, or into another data home).
    Result syncLaunchers();
    // Blocks until the app exits; the Result exit is the app's own exit code on success.
    Result run(const QString &appId, const QStringList &extraArgs);

    // Where things are (public for tests and the CLI).
    // --target group:<name> | host | worker:<name>. Fills *group for a group target; host and worker targets return
    // ExitUnavailable with the reason (never a silent fallback to a group).
    static Result parseTarget(const QString &target, bool acceptedHostWarning, QString *group);
    static QString dataRoot();
    static QString applicationsDir();
    static QString containerFor(const QString &group);
    static bool validGroupName(const QString &g);
    QStringList catalogDirs() const;

    struct Impl;  // public so the implementation's helpers can name it; not API
private:
    Impl *d;
};

} // namespace TaosAppGroups
