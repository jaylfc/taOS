// SPDX-License-Identifier: AGPL-3.0-or-later
// The person's own confirmation. Accepting a proprietary licence, and installing an update whose checksum only the
// download's own feed vouches for, need a single-use nonce that is scoped to that one action and subject. The Store
// (the person's UI) mints one in its own process when the person presses the button, and answers `consume` over a
// 0600 socket in a 0700 per-user directory, after the kernel-reported peer uid is checked on both ends. The CLI has no
// way to mint: a flag, an environment variable or free text can never stand in for the person.
//
// Limit (same as native/firmware): a process running as the same uid can still connect to the socket and can, in
// principle, serve its own. Keeping an agent away from it takes a separate uid or a sandbox that hides the directory.
#pragma once
#include <QString>
#include <memory>

namespace TaosAppGroups {

constexpr const char *kActionLicence = "licence.accept";     // subject: the app id
constexpr const char *kActionUpdate = "update.unverified";   // subject: <app id>@<version>#<sha256>

struct OwnerProof {
    bool ok = false;
    QString by;      // the account that confirmed, as the Store's session knows it (never free text)
    QString error;
};

// $TAOS_APPGROUPS_OWNER_DIR, else $XDG_RUNTIME_DIR/taos; empty when neither is set.
QString ownerDir();
QString ownerSocketPath();

// CLI side: asks the Store's socket to use the nonce up for exactly this action and subject.
OwnerProof consumeOwnerNonce(const QString &nonce, const QString &action, const QString &subject);

// Store side. One per process; mint() is in-process only (there is no mint request on the socket).
class OwnerGate {
public:
    OwnerGate();
    ~OwnerGate();
    OwnerGate(const OwnerGate &) = delete;
    OwnerGate &operator=(const OwnerGate &) = delete;
    bool start(QString *error);
    // A fresh nonce for one action on one subject; empty when the gate is not serving. Valid for two minutes, once.
    QString mint(const QString &action, const QString &subject);
    bool serving() const;

private:
    struct Impl;
    std::unique_ptr<Impl> d;
};

} // namespace TaosAppGroups
