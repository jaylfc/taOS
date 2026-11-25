// SPDX-License-Identifier: AGPL-3.0-or-later
// The "container desktop app" manifest (schema 1 and 2): data only. A recipe is a list of steps from a small allowlist; there is no
// shell in a manifest. parse() refuses anything outside the allowlist, so a manifest that loads is a manifest the
// engine is willing to run.
#pragma once
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVector>

namespace TaosAppGroups {

struct Step {
    QString op;          // apt-source | apt-install | download-tar | download-deb | symlink | suid-helper
    QJsonObject args;
};

struct Grants {
    bool network = false;
    bool gpu = false;        // the Intel render node only
    bool audio = false;      // PulseAudio/PipeWire-pulse socket
    bool documents = false;  // "Documents for apps" folder
    bool downloads = false;  // "Downloads for apps" folder
    bool nesting = false;    // security.nesting for the whole group
    bool camera = false;     // never granted today; declared so the Store can say so
    bool microphone = false; // never granted today
};

struct Manifest {
    QString id, name, description, publisher, homepage, licence, termsNote, termsUrl, version;
    int schema = 1;
    QString licenceUrl;          // schema 2: the licence text the person accepts
    bool licenceRequired = false; // schema 2 licence block with accept "required": install needs --accept-licence
    QString package;             // the apt package the recipe leaves installed (version lookup, update); optional
    QString updateFeedUrl;       // optional: the vendor's own JSON feed of current downloads (see update)
    QStringList data;            // the app's own state, relative to its home; empty means the whole home
    QString iconGlyph = QStringLiteral("app");  // code | browser | app (taOS generic glyphs)
    QStringList categories, arch, mimeTypes, launchArgv;
    QJsonObject launchEnv;
    QString windowAppId;
    QString binary;      // the command the install must leave on PATH (checked after the recipe)
    QVector<Step> recipe;
    Grants grants;
    QJsonObject raw;
};

// Hosts a recipe may talk to. Adding a vendor is a code change on purpose.
QStringList allowedHosts();
// https, on the allowlist, no credentials. `why` says what is wrong when false.
bool urlAllowed(const QString &url, QString *why);
bool parseManifest(const QByteArray &json, Manifest *out, QString *error);
// The manifest as the Store shows it (no recipe internals).
QJsonObject manifestSummary(const Manifest &m);

} // namespace TaosAppGroups
