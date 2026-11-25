// SPDX-License-Identifier: AGPL-3.0-or-later
#include "appgroups.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLockFile>
#include <QProcess>
#include <QRegularExpression>
#include <QSaveFile>
#include <QStandardPaths>
#include <QTextStream>
#include <QThread>

#include <unistd.h>

using namespace Qt::StringLiterals;

namespace TaosAppGroups {

namespace {

constexpr int kUid = 1000;  // the app user inside every group container
const QString kSessionDir = u"/var/lib/taos/session"_s;
const QString kBootMarker = u"/var/lib/taos/bootstrapped-1"_s;
const QString kHomeBase = u"/home/taos/apps"_s;

struct Proc {
    bool started = false;
    int code = -1;
    QString out, err;
    bool ok() const { return started && code == 0; }
};

QString hostHome() { return QDir::homePath(); }

QString envOr(const char *name, const QString &fallback)
{
    const QString v = qEnvironmentVariable(name);
    return v.isEmpty() ? fallback : v;
}

// The GPU grant is the render node of whichever adapter is present (Intel, AMD or NVIDIA): the lowest renderD* in the
// DRM directory. Never a card node. Empty when there is none (no GPU, or a machine without a render driver).
QString renderNode()
{
    const QDir dir(envOr("TAOS_APPGROUPS_DRI_DIR", u"/dev/dri"_s));
    const QStringList nodes = dir.entryList({u"renderD*"_s}, QDir::System | QDir::Files | QDir::NoDotAndDotDot | QDir::Readable, QDir::Name);
    return nodes.isEmpty() ? QString() : dir.absoluteFilePath(nodes.first());
}

// Text that goes into a one-line .desktop key: no newline or other control character may survive.
QString oneLine(const QString &s)
{
    QString o;
    for (const QChar c : s)
        o += (c.unicode() < 0x20 || c.unicode() == 0x7f || c.category() == QChar::Other_Control || c == QChar(0x2028) || c == QChar(0x2029)) ? QChar(u' ') : c;
    return o.simplified();
}

} // namespace

struct Engine::Impl {
    QString incus = envOr("TAOS_APPGROUPS_INCUS", u"incus"_s);
    QString userSocket = envOr("TAOS_APPGROUPS_USER_SOCKET", u"/var/lib/incus/unix.socket.user"_s);
    QString image = envOr("TAOS_APPGROUPS_IMAGE", u"images:debian/13"_s);
    QString mode;  // "per-user" or "admin-default-project"
    QProcessEnvironment penv = QProcessEnvironment::systemEnvironment();
    Progress prog;
    int stepN = 0, stepOf = 0;

    Impl()
    {
        // The per-person path first: incus-user gives a non admin person their own restricted project.
        const QString forced = qEnvironmentVariable("INCUS_SOCKET");
        if (!forced.isEmpty()) {
            mode = u"explicit-socket"_s;
        } else if (QFileInfo(userSocket).exists() && ::access(userSocket.toLocal8Bit().constData(), R_OK | W_OK) == 0) {
            penv.insert(u"INCUS_SOCKET"_s, userSocket);
            mode = u"per-user"_s;
        } else {
            mode = u"admin-default-project"_s;
            QTextStream(stderr) << "taos-appgroups: warning: no per-user incus socket (" << userSocket
                                << "): using the admin socket and the default project, so people on this machine are NOT isolated from each other\n";
        }
    }

    Proc incusRun(const QStringList &args, const QByteArray &in = {}, int timeoutMs = 120000) const
    {
        Proc r;
        QProcess p;
        p.setProcessEnvironment(penv);
        p.start(incus, args);
        if (!p.waitForStarted(5000))
            return r;
        r.started = true;
        if (!in.isEmpty())
            p.write(in);
        p.closeWriteChannel();
        if (!p.waitForFinished(timeoutMs)) {
            p.kill();
            p.waitForFinished(2000);
            r.err = u"timed out"_s;
            return r;
        }
        r.code = p.exitStatus() == QProcess::NormalExit ? p.exitCode() : -1;
        r.out = QString::fromUtf8(p.readAllStandardOutput());
        r.err = QString::fromUtf8(p.readAllStandardError());
        return r;
    }

    // incus exec inside a container as root, streaming output lines to the log and to progress.
    Proc exec(const QString &ct, const QStringList &argv, const QString &logFile = {}, const QStringList &env = {},
              int timeoutMs = 1800000, const QByteArray &in = {}) const
    {
        QStringList a = {u"exec"_s, ct};
        for (const QString &e : env)
            a << u"--env"_s << e;
        a << u"--"_s << argv;
        Proc r;
        QProcess p;
        p.setProcessEnvironment(penv);
        p.setProcessChannelMode(QProcess::MergedChannels);
        p.start(incus, a);
        if (!p.waitForStarted(5000))
            return r;
        r.started = true;
        if (!in.isEmpty())
            p.write(in);
        p.closeWriteChannel();
        QFile log(logFile);
        if (!logFile.isEmpty())
            (void)log.open(QIODevice::WriteOnly | QIODevice::Append);
        QElapsedTimer t;
        t.start();
        QByteArray buf;
        while (p.state() != QProcess::NotRunning) {
            p.waitForReadyRead(500);
            const QByteArray chunk = p.readAll();
            if (!chunk.isEmpty()) {
                if (log.isOpen())
                    log.write(chunk);
                buf += chunk;
                const QStringList lines = QString::fromUtf8(chunk).split(u'\n', Qt::SkipEmptyParts);
                if (prog && !lines.isEmpty())
                    prog(QJsonObject{{u"event"_s, u"detail"_s}, {u"text"_s, lines.last().left(160)}});
            }
            if (t.elapsed() > timeoutMs) {
                p.kill();
                p.waitForFinished(2000);
                r.err = u"timed out"_s;
                return r;
            }
        }
        buf += p.readAll();
        if (log.isOpen())
            log.write(p.readAll());
        r.code = p.exitStatus() == QProcess::NormalExit ? p.exitCode() : -1;
        r.out = QString::fromUtf8(buf);
        return r;
    }

    void step(const QString &text)
    {
        ++stepN;
        if (prog)
            prog(QJsonObject{{u"event"_s, u"step"_s}, {u"n"_s, stepN}, {u"of"_s, stepOf}, {u"text"_s, text}});
    }

    QJsonArray listContainers(bool *ok) const
    {
        const Proc r = incusRun({u"list"_s, u"--format"_s, u"json"_s});
        *ok = r.ok();
        return QJsonDocument::fromJson(r.out.toUtf8()).array();
    }
    QJsonObject containerInfo(const QString &name, bool *exists, bool *ok) const
    {
        const QJsonArray a = listContainers(ok);
        for (const QJsonValue &v : a)
            if (v.toObject().value(u"name"_s).toString() == name) {
                *exists = true;
                return v.toObject();
            }
        *exists = false;
        return {};
    }
};

// ------------------------------------------------------------------ paths and index

QString Engine::dataRoot()
{
    const QString e = qEnvironmentVariable("TAOS_APPGROUPS_DATA");
    if (!e.isEmpty())
        return e;
    return QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation) + u"/taos/appgroups"_s;
}

QString Engine::applicationsDir()
{
    return QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation) + u"/applications"_s;
}

bool Engine::validGroupName(const QString &g)
{
    return QRegularExpression(u"^[a-z][a-z0-9-]{0,28}$"_s).match(g).hasMatch();
}

QString Engine::containerFor(const QString &group)
{
    return group == QLatin1String(kDefaultGroup) ? QString::fromLatin1(kDefaultContainer) : u"appgroup-"_s + group;
}

Result Engine::parseTarget(const QString &target, bool acceptedHostWarning, QString *group)
{
    if (target.isEmpty() || target == u"group"_s) {
        *group = QString::fromLatin1(kDefaultGroup);
        return {};
    }
    if (target.startsWith(u"group:"_s)) {
        *group = target.mid(6);
        if (!validGroupName(*group))
            return Result::fail(ExitUsage, u"bad group name in --target"_s);
        return {};
    }
    if (target == u"host"_s) {
        Result r = Result::fail(ExitUnavailable,
            u"Installing directly on this computer is not available yet. It needs the signed maintenance helper for package installs, which does not exist; taOS will not use ad hoc root access. Install into an app group instead."_s);
        r.data[u"target"_s] = QJsonObject{{u"kind"_s, u"host"_s}, {u"warning_accepted"_s, acceptedHostWarning}};
        return r;
    }
    if (target.startsWith(u"worker:"_s) && target.size() > 7) {
        Result r = Result::fail(ExitUnavailable,
            u"Installing on a cluster worker is not available yet: showing the app's window on this device needs a window transport that is not built."_s);
        r.data[u"target"_s] = QJsonObject{{u"kind"_s, u"worker"_s}, {u"id"_s, target.mid(7)}};
        return r;
    }
    return Result::fail(ExitUsage, u"--target must be group:<name>, host or worker:<name>"_s);
}

QStringList Engine::catalogDirs() const
{
    QStringList l;
    const QString e = qEnvironmentVariable("TAOS_APPGROUPS_CATALOG");
    if (!e.isEmpty())
        l << e;
    l << dataRoot() + u"/catalog"_s << u":/appgroups/catalog"_s;
    return l;
}

namespace {

QString indexPath() { return Engine::dataRoot() + u"/installed.json"_s; }

QJsonObject readIndex()
{
    QFile f(indexPath());
    if (!f.open(QIODevice::ReadOnly))
        return QJsonObject{{u"apps"_s, QJsonObject()}};
    const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
    return o.isEmpty() ? QJsonObject{{u"apps"_s, QJsonObject()}} : o;
}

bool writeIndex(const QJsonObject &o, QString *err)
{
    QDir().mkpath(Engine::dataRoot());
    QLockFile lock(indexPath() + u".lock"_s);
    lock.setStaleLockTime(30000);
    if (!lock.tryLock(5000)) {
        *err = u"the app index is locked by another taos-appgroups"_s;
        return false;
    }
    QSaveFile f(indexPath());
    if (!f.open(QIODevice::WriteOnly) || f.write(QJsonDocument(o).toJson()) < 0 || !f.commit()) {
        *err = u"cannot write %1"_s.arg(indexPath());
        return false;
    }
    return true;
}

bool loadManifestFile(const QString &path, Manifest *m, QString *err)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        *err = u"cannot read %1"_s.arg(path);
        return false;
    }
    return parseManifest(f.readAll(), m, err);
}

} // namespace

Engine::Engine() : d(new Impl) {}

static bool findManifest(const Engine &e, const QString &id, Manifest *m, QString *err)
{
    if (!QRegularExpression(u"^[a-z][a-z0-9-]{1,40}$"_s).match(id).hasMatch()) {
        *err = u"not a valid app id: %1"_s.arg(id);
        return false;
    }
    for (const QString &dir : e.catalogDirs()) {
        const QString p = dir + u"/"_s + id + u".json"_s;
        if (QFileInfo::exists(p))
            return loadManifestFile(p, m, err);
    }
    *err = u"no app called %1 in the catalogue"_s.arg(id);
    return false;
}

// ------------------------------------------------------------------ catalogue, list

Result Engine::catalog()
{
    QJsonArray apps;
    QStringList seen;
    for (const QString &dir : catalogDirs()) {
        for (const QString &fn : QDir(dir).entryList({u"*.json"_s}, QDir::Files, QDir::Name)) {
            Manifest m;
            QString err;
            const QString id = fn.chopped(5);
            if (seen.contains(id))
                continue;
            seen << id;
            if (!loadManifestFile(dir + u"/"_s + fn, &m, &err)) {
                apps.append(QJsonObject{{u"id"_s, id}, {u"invalid"_s, err}});
                continue;
            }
            apps.append(manifestSummary(m));
        }
    }
    Result r;
    r.data[u"apps"_s] = apps;
    return r;
}

Result Engine::list(bool available)
{
    Result r;
    const QJsonObject apps = readIndex().value(u"apps"_s).toObject();
    QJsonArray a;
    for (auto it = apps.begin(); it != apps.end(); ++it) {
        const QJsonObject rec = it.value().toObject();
        Manifest m;
        QString err;
        parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &err);
        QJsonObject o = manifestSummary(m);
        o[u"group"_s] = rec.value(u"group"_s);
        o[u"installed_at"_s] = rec.value(u"installed_at"_s);
        o[u"installed_version"_s] = rec.value(u"installed_version"_s);
        o[u"licence_accepted"_s] = rec.value(u"licence_accepted"_s);
        o[u"installed"_s] = true;
        a.append(o);
    }
    r.data[u"apps"_s] = a;
    if (available) {
        QJsonArray av;
        for (const QJsonValue &v : catalog().data.value(u"apps"_s).toArray())
            if (!apps.contains(v.toObject().value(u"id"_s).toString()))
                av.append(v);
        r.data[u"available"_s] = av;
    }
    return r;
}

// ------------------------------------------------------------------ groups

namespace {

// "My apps" for the default group; else the name the person gave; else the id made readable ("dev-apps" -> "Dev apps").
QString displayNameOf(const QJsonObject &c, const QString &g)
{
    if (g == QLatin1String(kDefaultGroup))
        return QString::fromLatin1(kDefaultDisplayName);
    const QString given = c.value(u"config"_s).toObject().value(u"user.taos.display_name"_s).toString();
    if (!given.isEmpty())
        return given;
    QString n = g;
    n.replace(u'-', u' ');
    return n.isEmpty() ? n : n.at(0).toUpper() + n.mid(1);
}

// The person's own groups only: containers this person created (owner uid) and tagged as app groups.
bool ownedGroup(const QJsonObject &c)
{
    const QJsonObject cfg = c.value(u"config"_s).toObject();
    return cfg.value(u"user.taos.kind"_s).toString() == u"app-group"_s
        && cfg.value(u"user.taos.owner"_s).toString() == QString::number(::getuid());
}

} // namespace

Result Engine::groups()
{
    bool ok = false;
    const QJsonArray all = d->listContainers(&ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus (is it installed, and is this account allowed to use it?)"_s);
    const QJsonObject idx = readIndex().value(u"apps"_s).toObject();
    QJsonArray out;
    for (const QJsonValue &v : all) {
        const QJsonObject c = v.toObject();
        if (!ownedGroup(c))
            continue;
        const QString g = c.value(u"config"_s).toObject().value(u"user.taos.group"_s).toString();
        QJsonArray apps;
        for (auto it = idx.begin(); it != idx.end(); ++it)
            if (it.value().toObject().value(u"group"_s).toString() == g)
                apps.append(it.key());
        out.append(QJsonObject{{u"name"_s, g},
                               {u"display_name"_s, displayNameOf(c, g)},
                               {u"container"_s, c.value(u"name"_s)},
                               {u"state"_s, c.value(u"status"_s).toString().toLower()},
                               {u"apps"_s, apps}});
    }
    Result r;
    r.data[u"groups"_s] = out;
    return r;
}

// Everything below needs Impl; keep it in free functions taking Impl.
namespace {

QString firstManagedNetwork(const Engine::Impl *d)
{
    const Proc r = d->incusRun({u"profile"_s, u"device"_s, u"get"_s, u"default"_s, u"eth0"_s, u"network"_s});
    if (r.ok() && !r.out.trimmed().isEmpty())
        return r.out.trimmed();
    return u"incusbr0"_s;
}

QString poolName(const Engine::Impl *d)
{
    const Proc r = d->incusRun({u"profile"_s, u"device"_s, u"get"_s, u"default"_s, u"root"_s, u"pool"_s});
    return r.ok() && !r.out.trimmed().isEmpty() ? r.out.trimmed() : u"default"_s;
}

Result ensureProfile(const Engine::Impl *d)
{
    if (d->incusRun({u"profile"_s, u"show"_s, QString::fromLatin1(kProfile)}).ok())
        return {};
    if (!d->incusRun({u"profile"_s, u"create"_s, QString::fromLatin1(kProfile)}).ok())
        return Result::fail(ExitIncus, u"cannot create the %1 profile"_s.arg(QLatin1String(kProfile)));
    // Static, group independent: the root disk, read only host fonts and themes, no privilege, no nesting.
    // Grant dependent devices (network, GPU, audio, shared folders) are added to the container by applyGrants.
    QString y = u"config:\n  security.privileged: \"false\"\n  security.nesting: \"false\"\n  user.taos.kind: app-group-profile\ndevices:\n"_s;
    y += u"  root:\n    type: disk\n    path: /\n    pool: %1\n"_s.arg(poolName(d));
    if (QFileInfo(u"/usr/share/fonts"_s).isDir())
        y += u"  host-fonts:\n    type: disk\n    source: /usr/share/fonts\n    path: /usr/local/share/fonts/host\n    readonly: \"true\"\n"_s;
    if (QFileInfo(u"/usr/share/taos/themes"_s).isDir())
        y += u"  taos-themes:\n    type: disk\n    source: /usr/share/taos/themes\n    path: /usr/share/taos/themes\n    readonly: \"true\"\n"_s;
    const Proc r = d->incusRun({u"profile"_s, u"edit"_s, QString::fromLatin1(kProfile)}, y.toUtf8());
    if (!r.ok())
        return Result::fail(ExitIncus, u"cannot configure the profile: %1"_s.arg(r.err.trimmed()));
    return {};
}

bool hasDevice(const QJsonObject &c, const QString &name)
{
    return c.value(u"devices"_s).toObject().contains(name);
}

bool addDevice(const Engine::Impl *d, const QString &ct, const QString &name, const QStringList &args, QString *err)
{
    const Proc r = d->incusRun(QStringList{u"config"_s, u"device"_s, u"add"_s, ct, name} + args);
    if (!r.ok())
        *err = r.err.trimmed();
    return r.ok();
}

bool removeDevice(const Engine::Impl *d, const QString &ct, const QString &name, QString *err)
{
    const Proc r = d->incusRun({u"config"_s, u"device"_s, u"remove"_s, ct, name});
    if (!r.ok())
        *err = r.err.trimmed();
    return r.ok();
}

Grants unionGrants(const QString &group, const QString &withApp = {}, const Grants *extra = nullptr)
{
    Grants g;
    const QJsonObject apps = readIndex().value(u"apps"_s).toObject();
    auto merge = [&](const Grants &x) {
        g.network |= x.network; g.gpu |= x.gpu; g.audio |= x.audio; g.documents |= x.documents;
        g.downloads |= x.downloads; g.nesting |= x.nesting;
    };
    for (auto it = apps.begin(); it != apps.end(); ++it) {
        const QJsonObject rec = it.value().toObject();
        if (rec.value(u"group"_s).toString() != group || it.key() == withApp)
            continue;
        Manifest m;
        QString e;
        if (parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &e))
            merge(m.grants);
    }
    if (extra)
        merge(*extra);
    return g;
}

QString docsHost() { return hostHome() + u"/Documents for apps"_s; }
QString downloadsHost() { return hostHome() + u"/Downloads for apps"_s; }

// Make the container's devices match the grants. forceNetwork keeps the NIC while an install needs to download.
Result applyGrants(const Engine::Impl *d, const QString &ct, const Grants &g, bool forceNetwork)
{
    bool ok = false, exists = false;
    QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok || !exists)
        return Result::fail(ExitNotFound, u"container %1 not found"_s.arg(ct));
    QString err;
    auto want = [&](const QString &name, bool on, const QStringList &args) -> bool {
        const bool has = hasDevice(c, name);
        if (on && !has)
            return addDevice(d, ct, name, args, &err);
        if (!on && has)
            return removeDevice(d, ct, name, &err);
        return true;
    };
    if (g.documents)
        QDir().mkpath(docsHost());
    if (g.downloads)
        QDir().mkpath(downloadsHost());
    bool good = want(u"eth0"_s, g.network || forceNetwork, {u"nic"_s, u"network="_s + firstManagedNetwork(d), u"name=eth0"_s});
    const QString node = g.gpu ? renderNode() : QString();
    if (g.gpu && node.isEmpty())
        QTextStream(stderr) << "taos-appgroups: no GPU render node found (no /dev/dri/renderD*): the app runs without GPU access\n";
    good = good && want(u"gpu-intel"_s, false, {});   // the device name of earlier versions (Intel only)
    good = good && want(u"gpu"_s, !node.isEmpty(),
                        {u"unix-char"_s, u"source="_s + node, u"path=/dev/dri/"_s + QFileInfo(node).fileName(), u"uid=1000"_s, u"gid=1000"_s, u"mode=0660"_s});
    good = good && want(u"documents"_s, g.documents,
                        {u"disk"_s, u"source="_s + docsHost(), u"path=/mnt/taos/documents"_s, u"shift=true"_s});
    good = good && want(u"downloads"_s, g.downloads,
                        {u"disk"_s, u"source="_s + downloadsHost(), u"path=/mnt/taos/downloads"_s, u"shift=true"_s});
    if (!good)
        return Result::fail(ExitIncus, u"cannot apply grants to %1: %2"_s.arg(ct, err));
    const QString nest = c.value(u"config"_s).toObject().value(u"security.nesting"_s).toString();
    if ((nest == u"true"_s) != g.nesting) {
        if (!d->incusRun({u"config"_s, u"set"_s, ct, u"security.nesting="_s + (g.nesting ? u"true"_s : u"false"_s)}).ok())
            return Result::fail(ExitIncus, u"cannot set nesting on %1"_s.arg(ct));
    }
    return {};
}

// The system pieces every group has. An app's removal never takes these out, whatever its recipe lists.
QStringList basePackages()
{
    return {u"ca-certificates"_s, u"curl"_s, u"gnupg"_s, u"xdg-utils"_s, u"libgl1-mesa-dri"_s, u"libegl1"_s, u"libgbm1"_s,
            u"mesa-vulkan-drivers"_s, u"libegl-mesa0"_s, u"libgl1"_s, u"libglx-mesa0"_s, u"libgles2"_s, u"libdrm2"_s, u"fontconfig"_s, u"fonts-dejavu-core"_s, u"fonts-noto-core"_s, u"libpulse0"_s, u"dbus"_s,
            u"dbus-user-session"_s, u"libwayland-client0"_s, u"libxkbcommon0"_s, u"adwaita-icon-theme"_s};
}

Result bootstrap(Engine::Impl *d, const QString &ct)
{
    if (d->exec(ct, {u"test"_s, u"-f"_s, kBootMarker}).ok())
        return {};
    d->step(u"Preparing the app group (system packages)"_s);
    const QString log = Engine::dataRoot() + u"/logs/bootstrap-"_s + ct + u".log"_s;
    QDir().mkpath(QFileInfo(log).absolutePath());
    const QStringList env = {u"DEBIAN_FRONTEND=noninteractive"_s};
    // Wait for the network inside the container (dhcp) for up to a minute.
    bool net = false;
    for (int i = 0; i < 30 && !net; ++i) {
        net = d->exec(ct, {u"getent"_s, u"hosts"_s, u"deb.debian.org"_s}).ok();
        if (!net)
            QThread::sleep(2);
    }
    if (!net)
        return Result::fail(ExitStep, u"the container has no network (cannot resolve deb.debian.org)"_s);
    if (!d->exec(ct, {u"apt-get"_s, u"update"_s}, log, env).ok())
        return Result::fail(ExitStep, u"apt-get update failed, see %1"_s.arg(log));
    // Runtime pieces every desktop app needs: TLS roots, curl and gpg for recipe steps, Mesa for the GPU,
    // fonts, the pulse client, a session bus, and the usual helpers.
    const QStringList pk = basePackages();
    if (!d->exec(ct, QStringList{u"apt-get"_s, u"install"_s, u"-y"_s, u"--no-install-recommends"_s} + pk, log, env).ok())
        return Result::fail(ExitStep, u"installing the base packages failed, see %1"_s.arg(log));
    const char *script = "id -u taos >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash taos; "
                         "mkdir -p /var/lib/taos/session /mnt/taos/documents /mnt/taos/downloads /home/taos/apps; "
                         "chmod 755 /var/lib/taos /var/lib/taos/session; chown -R 1000:1000 /home/taos; "
                         "fc-cache -f >/dev/null 2>&1; touch ";
    const Proc r = d->exec(ct, {u"sh"_s, u"-c"_s, QString::fromLatin1(script) + kBootMarker}, log);
    if (!r.ok())
        return Result::fail(ExitStep, u"preparing the app user failed, see %1"_s.arg(log));
    return {};
}

Result ensureGroupImpl(Engine::Impl *d, const QString &group, bool *created)
{
    *created = false;
    const QString ct = Engine::containerFor(group);
    bool ok = false, exists = false;
    QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus (is it installed, and is this account allowed to use it?)"_s);
    if (exists && !ownedGroup(c))
        return Result::fail(ExitConflict, u"a container called %1 exists but is not one of your app groups"_s.arg(ct));
    if (!exists) {
        if (Result r = ensureProfile(d); !r.ok)
            return r;
        d->step(u"Creating the %1 container"_s.arg(ct));
        const Proc r = d->incusRun({u"init"_s, d->image, ct, u"-p"_s, QString::fromLatin1(kProfile)}, {}, 900000);
        if (!r.ok())
            return Result::fail(ExitIncus, u"cannot create container %1: %2"_s.arg(ct, r.err.trimmed()));
        *created = true;
        for (const auto &kv : QList<QPair<QString, QString>>{{u"user.taos.kind"_s, u"app-group"_s},
                                                              {u"user.taos.group"_s, group},
                                                              {u"user.taos.owner"_s, QString::number(::getuid())}})
            d->incusRun({u"config"_s, u"set"_s, ct, kv.first + u"="_s + kv.second});
        // The NIC is needed to install the base system; applyGrants drops it again when no app needs the network.
        QString err;
        if (!addDevice(d, ct, u"eth0"_s, {u"nic"_s, u"network="_s + firstManagedNetwork(d), u"name=eth0"_s}, &err))
            return Result::fail(ExitIncus, u"cannot add the network device: %1"_s.arg(err));
        c = d->containerInfo(ct, &exists, &ok);
    }
    if (c.value(u"status"_s).toString() != u"Running"_s) {
        const Proc r = d->incusRun({u"start"_s, ct}, {}, 120000);
        if (!r.ok())
            return Result::fail(ExitIncus, u"cannot start %1: %2"_s.arg(ct, r.err.trimmed()));
    }
    if (Result r = bootstrap(d, ct); !r.ok)
        return r;
    return applyGrants(d, ct, unionGrants(group), false);
}

} // namespace

Result Engine::ensureDefault(const Progress &p)
{
    d->prog = p;
    d->stepN = 0;
    d->stepOf = 4;
    d->step(u"Checking the apps container"_s);
    bool created = false;
    Result r = ensureGroupImpl(d, QString::fromLatin1(kDefaultGroup), &created);
    if (!r.ok)
        return r;
    r.data[u"group"_s] = QString::fromLatin1(kDefaultGroup);
    r.data[u"display_name"_s] = QString::fromLatin1(kDefaultDisplayName);
    r.data[u"container"_s] = QString::fromLatin1(kDefaultContainer);
    r.data[u"created"_s] = created;
    return r;
}

Result Engine::groupCreate(const QString &name, const Progress &p, const QString &displayName)
{
    if (!validGroupName(name) || name == QLatin1String(kDefaultGroup))
        return Result::fail(ExitUsage, u"group names are lower case letters, digits and dashes, starting with a letter (and not \"default\")"_s);
    if (displayName != oneLine(displayName) || displayName.size() > 40)
        return Result::fail(ExitUsage, u"the display name must be one line of at most 40 characters"_s);
    d->prog = p;
    d->stepN = 0;
    d->stepOf = 4;
    bool created = false;
    Result r = ensureGroupImpl(d, name, &created);
    if (!r.ok)
        return r;
    if (!created)
        return Result::fail(ExitConflict, u"the group %1 already exists"_s.arg(name));
    if (!displayName.isEmpty() && !d->incusRun({u"config"_s, u"set"_s, containerFor(name), u"user.taos.display_name="_s + displayName}).ok())
        return Result::fail(ExitIncus, u"the group was created but its display name could not be saved"_s);
    r.data[u"group"_s] = name;
    r.data[u"display_name"_s] = displayName.isEmpty() ? name : displayName;
    r.data[u"container"_s] = containerFor(name);
    return r;
}

// ------------------------------------------------------------------ install

namespace {

QString sourcesFile(const QString &name) { return u"/etc/apt/sources.list.d/"_s + name + u".sources"_s; }
QString keyFile(const QString &name) { return u"/etc/apt/keyrings/"_s + name + u".gpg"_s; }

bool fingerprintsInclude(const QString &gpgColons, const QString &want)
{
    for (const QString &l : gpgColons.split(u'\n'))
        if (l.startsWith(u"fpr:"_s) && l.split(u':').value(9) == want)
            return true;
    return false;
}

Result runStep(Engine::Impl *d, const QString &ct, const Manifest &m, const Step &s, const QString &log)
{
    const QStringList env = {u"DEBIAN_FRONTEND=noninteractive"_s};
    const QJsonObject a = s.args;
    auto fail = [&](const QString &what) { return Result::fail(ExitStep, what + u" (log: "_s + log + u")"_s); };
    if (s.op == u"apt-source"_s) {
        const QString name = a.value(u"name"_s).toString();
        const QString tmp = u"/var/tmp/taos-key-"_s + name;
        if (!d->exec(ct, {u"curl"_s, u"-fsSL"_s, u"--retry"_s, u"3"_s, u"-o"_s, tmp, a.value(u"key_url"_s).toString()}, log).ok())
            return fail(u"could not download the signing key"_s);
        const QString want = a.value(u"key_fingerprint"_s).toString();
        const Proc fp = d->exec(ct, {u"gpg"_s, u"--show-keys"_s, u"--with-colons"_s, tmp}, {});
        if (!fp.ok() || !fingerprintsInclude(fp.out, want))
            return Result::fail(ExitStep, u"the signing key does not have the expected fingerprint %1; refusing to trust it"_s.arg(want));
        d->exec(ct, {u"mkdir"_s, u"-p"_s, u"/etc/apt/keyrings"_s});
        if (!d->exec(ct, {u"gpg"_s, u"--batch"_s, u"--yes"_s, u"--dearmor"_s, u"-o"_s, keyFile(name), tmp}, log).ok())
            return fail(u"could not prepare the signing key"_s);
        const Proc fp2 = d->exec(ct, {u"gpg"_s, u"--show-keys"_s, u"--with-colons"_s, keyFile(name)}, {});
        if (!fingerprintsInclude(fp2.out, want))
            return Result::fail(ExitStep, u"the stored key does not match the expected fingerprint"_s);
        QString src = u"Types: deb\nURIs: %1\nSuites: %2\nComponents: %3\nSigned-By: %4\n"_s
                          .arg(a.value(u"uri"_s).toString(), a.value(u"suite"_s).toString(),
                               QStringList([&] { QStringList l; for (const QJsonValue &v : a.value(u"components"_s).toArray()) l << v.toString(); return l; }()).join(u' '),
                               keyFile(name));
        if (!a.value(u"arch"_s).toString().isEmpty())
            src += u"Architectures: %1\n"_s.arg(a.value(u"arch"_s).toString());
        if (!d->exec(ct, {u"tee"_s, sourcesFile(name)}, log, {}, 60000, src.toUtf8()).ok())
            return fail(u"could not write the apt source"_s);
        if (!d->exec(ct, {u"apt-get"_s, u"update"_s}, log, env).ok())
            return fail(u"apt-get update failed after adding the source"_s);
        return {};
    }
    if (s.op == u"etc-default"_s) {
        // The manifest parser only lets plain tokens through, so a value can never close its quotes.
        QString body;
        const QJsonObject v = a.value(u"values"_s).toObject();
        for (auto it = v.begin(); it != v.end(); ++it)
            body += u"%1=\"%2\"\n"_s.arg(it.key(), it.value().toString());
        return d->exec(ct, {u"tee"_s, u"/etc/default/"_s + a.value(u"name"_s).toString()}, log, {}, 60000, body.toUtf8()).ok()
                   ? Result() : fail(u"could not write /etc/default/%1"_s.arg(a.value(u"name"_s).toString()));
    }
    if (s.op == u"apt-install"_s) {
        QStringList pk;
        for (const QJsonValue &v : a.value(u"packages"_s).toArray())
            pk << v.toString();
        if (!d->exec(ct, QStringList{u"apt-get"_s, u"install"_s, u"-y"_s, u"--no-install-recommends"_s} + pk, log, env).ok())
            return fail(u"installing %1 failed"_s.arg(pk.join(u' ')));
        return {};
    }
    if (s.op == u"download-tar"_s || s.op == u"download-deb"_s) {
        // apt only accepts a local package whose name ends in .deb
        const QString tmp = u"/var/tmp/taos-dl-"_s + m.id + (s.op == u"download-deb"_s ? u".deb"_s : QString());
        if (!d->exec(ct, {u"curl"_s, u"-fL"_s, u"--retry"_s, u"3"_s, u"-o"_s, tmp, a.value(u"url"_s).toString()}, log).ok())
            return fail(u"the download failed"_s);
        const Proc sum = d->exec(ct, {u"sha256sum"_s, tmp}, {});
        const QString got = sum.out.left(64);
        if (!sum.ok() || got != a.value(u"sha256"_s).toString()) {
            d->exec(ct, {u"rm"_s, u"-f"_s, tmp});
            return Result::fail(ExitStep, u"the download does not match the pinned checksum (got %1); it was deleted and not installed"_s.arg(got));
        }
        Proc r;
        if (s.op == u"download-tar"_s) {
            const QString dest = a.value(u"dest"_s).toString();
            d->exec(ct, {u"rm"_s, u"-rf"_s, dest});
            d->exec(ct, {u"mkdir"_s, u"-p"_s, dest});
            r = d->exec(ct, {u"tar"_s, u"-xzf"_s, tmp, u"-C"_s, dest, u"--strip-components="_s + QString::number(a.value(u"strip"_s).toInt(0))}, log);
        } else {
            r = d->exec(ct, {u"apt-get"_s, u"install"_s, u"-y"_s, tmp}, log, env);
        }
        d->exec(ct, {u"rm"_s, u"-f"_s, tmp});
        return r.ok() ? Result() : fail(u"unpacking the download failed"_s);
    }
    if (s.op == u"symlink"_s)
        return d->exec(ct, {u"ln"_s, u"-sfn"_s, a.value(u"target"_s).toString(), a.value(u"link"_s).toString()}, log).ok()
                   ? Result() : fail(u"could not create the command link"_s);
    if (s.op == u"suid-helper"_s) {
        const QString p = a.value(u"path"_s).toString();
        return d->exec(ct, {u"chown"_s, u"root:root"_s, p}, log).ok() && d->exec(ct, {u"chmod"_s, u"4755"_s, p}, log).ok()
                   ? Result() : fail(u"could not set up the sandbox helper"_s);
    }
    return Result::fail(ExitRefused, u"unknown step "_s + s.op);
}

QString desktopPath(const QString &id) { return Engine::applicationsDir() + u"/taos-appgroups-"_s + id + u".desktop"_s; }
QString iconPath(const QString &id) { return Engine::dataRoot() + u"/icons/"_s + id + u".svg"_s; }

QString selfExec()
{
    const QString e = qEnvironmentVariable("TAOS_APPGROUPS_EXEC");
    return e.isEmpty() ? QCoreApplication::applicationFilePath() : e;
}

QString desktopQuote(const QString &s)
{
    if (!s.contains(u' ') && !s.contains(u'"'))
        return s;
    QString q = s;
    q.replace(u'\\', u"\\\\"_s).replace(u'"', u"\\\""_s);
    return u'"' + q + u'"';
}

bool writeLauncher(const Manifest &m, const QString &group, QString *err)
{
    QDir().mkpath(QFileInfo(iconPath(m.id)).absolutePath());
    QFile src(u":/appgroups/icons/"_s + m.iconGlyph + u".svg"_s);
    QFile::remove(iconPath(m.id));
    if (!src.open(QIODevice::ReadOnly) || !QFile::copy(src.fileName(), iconPath(m.id))) {
        *err = u"cannot write the icon"_s;
        return false;
    }
    QFile::setPermissions(iconPath(m.id), QFile::ReadOwner | QFile::WriteOwner | QFile::ReadGroup | QFile::ReadOther);
    QDir().mkpath(Engine::applicationsDir());
    QString t = u"[Desktop Entry]\nType=Application\nVersion=1.5\n"_s;
    t += u"Name=%1\nComment=%2\n"_s.arg(oneLine(m.name), oneLine(m.description).left(120));
    t += u"Exec=%1 run %2 %U\n"_s.arg(desktopQuote(selfExec()), m.id);
    t += u"Icon=%1\nTerminal=false\nStartupWMClass=%2\n"_s.arg(iconPath(m.id), m.windowAppId);
    t += u"Categories=%1\n"_s.arg((m.categories.isEmpty() ? QStringList{u"Utility"_s} : m.categories).join(u';') + u';');
    if (!m.mimeTypes.isEmpty())
        t += u"MimeType=%1;\n"_s.arg(m.mimeTypes.join(u';'));
    t += u"X-taOS-AppId=%1\nX-taOS-ContainerApp=%2\nX-taOS-AppGroup=%3\n"_s.arg(m.windowAppId, m.id, group);
    QSaveFile f(desktopPath(m.id));
    if (!f.open(QIODevice::WriteOnly) || f.write(t.toUtf8()) < 0 || !f.commit()) {
        *err = u"cannot write the launcher entry"_s;
        return false;
    }
    return true;
}

} // namespace

namespace {

// Is any process of this app running in the container? Every launch sets HOME to the app's own home, so a process
// whose environment says so belongs to the app. false and *known=false when that could not be found out (an unknown
// answer is never reported as "not running").
bool appRunning(const Engine::Impl *d, const QString &ct, const QString &appId, bool *known)
{
    const QString home = kHomeBase + u"/"_s + appId;
    const QString script = u"for p in /proc/[0-9]*; do if tr '\\0' '\\n' < $p/environ 2>/dev/null | grep -qx 'HOME=%1'; then echo $p; fi; done"_s.arg(home);
    const Proc r = d->exec(ct, {u"sh"_s, u"-c"_s, script});
    *known = r.ok();
    return r.ok() && !r.out.trimmed().isEmpty();
}

QString packageVersion(const Engine::Impl *d, const QString &ct, const QString &package)
{
    if (package.isEmpty())
        return {};
    const Proc r = d->exec(ct, {u"dpkg-query"_s, u"-W"_s, u"-f=${Version}"_s, package});
    return r.ok() ? r.out.trimmed() : QString();
}

QString debSha(const Manifest &m)
{
    for (const Step &s : m.recipe)
        if (s.op == u"download-deb"_s)
            return s.args.value(u"sha256"_s).toString();
    return {};
}

bool hasOp(const Manifest &m, const QString &op)
{
    for (const Step &s : m.recipe)
        if (s.op == op)
            return true;
    return false;
}

// Undo the recipe's files and packages in a container (not the app's data, launcher or index entry). `apps` is the index
// as it stands, with this app already pointing at its new group, so packages other apps of the group share are kept.
Result uninstallRecipe(Engine::Impl *d, const QString &ct, const Manifest &m, const QString &group, const QJsonObject &apps, const QString &log)
{
    const QString appId = m.id;
    const QStringList env = {u"DEBIAN_FRONTEND=noninteractive"_s};
    for (const Step &s : m.recipe) {
        Proc r;
        if (s.op == u"apt-install"_s) {
            QStringList pk;
            for (const QJsonValue &v : s.args.value(u"packages"_s).toArray()) {
                const QString name = v.toString();
                bool shared = basePackages().contains(name);
                for (auto it = apps.begin(); it != apps.end() && !shared; ++it) {
                    if (it.key() == appId || it.value().toObject().value(u"group"_s).toString() != group)
                        continue;
                    for (const QJsonValue &st : it.value().toObject().value(u"manifest"_s).toObject().value(u"recipe"_s).toArray())
                        if (st.toObject().value(u"packages"_s).toArray().contains(v))
                            shared = true;
                }
                if (!shared)
                    pk << name;
            }
            if (pk.isEmpty())
                continue;
            // Mark the app's packages as automatic and let apt remove what nothing else needs. Removing them by
            // name would also remove anything that depends on them, including the base graphics stack.
            r = d->exec(ct, QStringList{u"apt-mark"_s, u"auto"_s} + pk, log, env);
            if (r.ok())
                r = d->exec(ct, {u"apt-get"_s, u"autoremove"_s, u"-y"_s, u"--purge"_s}, log, env);
            if (!r.ok())
                return Result::fail(ExitStep, u"removing the packages failed (log: %1)"_s.arg(log));
        } else if (s.op == u"download-deb"_s) {
            r = d->exec(ct, {u"apt-get"_s, u"remove"_s, u"-y"_s, u"--purge"_s, s.args.value(u"package"_s).toString()}, log, env);
            if (r.ok())
                r = d->exec(ct, {u"apt-get"_s, u"autoremove"_s, u"-y"_s, u"--purge"_s}, log, env);
            if (!r.ok())
                return Result::fail(ExitStep, u"removing the package failed (log: %1)"_s.arg(log));
        } else if (s.op == u"download-tar"_s) {
            d->exec(ct, {u"rm"_s, u"-rf"_s, s.args.value(u"dest"_s).toString()}, log);
        } else if (s.op == u"symlink"_s) {
            d->exec(ct, {u"rm"_s, u"-f"_s, s.args.value(u"link"_s).toString()}, log);
        } else if (s.op == u"etc-default"_s) {
            d->exec(ct, {u"rm"_s, u"-f"_s, u"/etc/default/"_s + s.args.value(u"name"_s).toString()}, log);
        } else if (s.op == u"apt-source"_s) {
            bool used = false;
            for (auto it = apps.begin(); it != apps.end(); ++it) {
                if (it.key() == appId || it.value().toObject().value(u"group"_s).toString() != group)
                    continue;
                for (const QJsonValue &st : it.value().toObject().value(u"manifest"_s).toObject().value(u"recipe"_s).toArray())
                    if (st.toObject().value(u"op"_s).toString() == u"apt-source"_s
                        && st.toObject().value(u"name"_s).toString() == s.args.value(u"name"_s).toString())
                        used = true;
            }
            if (!used) {
                d->exec(ct, {u"rm"_s, u"-f"_s, sourcesFile(s.args.value(u"name"_s).toString()), keyFile(s.args.value(u"name"_s).toString())}, log);
                d->exec(ct, {u"apt-get"_s, u"update"_s}, log, env);
            }
        }
    }
    return {};
}

} // namespace

// Everything an install does inside the container, up to a verified binary: grants, recipe, checks, the app home. No
// launcher and no index entry (the caller writes those once it is satisfied), so move can stage an app in a second
// group without the app ever being listed twice.
Result Engine::runRecipe(const Manifest &m, const QString &group, bool *created)
{
    if (Result r = ensureGroupImpl(d, group, created); !r.ok)
        return r;
    const QString ct = containerFor(group);
    d->step(u"Setting up access for %1"_s.arg(m.name));
    // Downloads need the network whether or not the finished app does.
    if (Result r = applyGrants(d, ct, unionGrants(group, m.id, &m.grants), true); !r.ok)
        return r;
    const QString log = dataRoot() + u"/logs/install-"_s + m.id + u".log"_s;
    QDir().mkpath(QFileInfo(log).absolutePath());
    QFile::remove(log);
    for (const Step &s : m.recipe) {
        QString text;
        if (s.op == u"apt-source"_s) text = u"Adding the publisher's software source"_s;
        else if (s.op == u"apt-install"_s) text = u"Installing system packages"_s;
        else if (s.op == u"download-tar"_s || s.op == u"download-deb"_s) text = u"Downloading %1 from the publisher"_s.arg(m.name);
        else text = u"Finishing set-up"_s;
        d->step(text);
        if (Result r = runStep(d, ct, m, s, log); !r.ok) {
            applyGrants(d, ct, unionGrants(group), false);
            return r;
        }
    }
    d->step(u"Checking the install"_s);
    if (!d->exec(ct, {u"test"_s, u"-x"_s, m.binary}).ok()) {
        applyGrants(d, ct, unionGrants(group), false);
        return Result::fail(ExitStep, u"the install finished but %1 is not there (log: %2)"_s.arg(m.binary, log));
    }
    d->exec(ct, {u"install"_s, u"-d"_s, u"-o"_s, u"1000"_s, u"-g"_s, u"1000"_s, kHomeBase + u"/"_s + m.id});
    return {};
}

Result Engine::install(const QString &appId, const QString &group, const Progress &p, const InstallOptions &opt)
{
    d->prog = p;
    d->stepN = 0;
    Manifest m;
    QString err;
    if (!findManifest(*this, appId, &m, &err))
        return Result::fail(err.startsWith(u"no app"_s) ? ExitNotFound : ExitRefused, err);
    if (!validGroupName(group))
        return Result::fail(ExitUsage, u"bad group name"_s);
    const QString arch = QSysInfo::currentCpuArchitecture() == u"x86_64"_s ? u"amd64"_s : u"arm64"_s;
    if (!m.arch.contains(arch))
        return Result::fail(ExitRefused, u"%1 is not available for this machine (%2)"_s.arg(m.name, arch));
    if (m.grants.camera || m.grants.microphone)
        return Result::fail(ExitRefused, u"camera and microphone grants are not supported yet"_s);
    // The licence gate comes before anything is created or downloaded.
    QJsonObject accepted;
    if (m.licenceRequired) {
        if (opt.acceptLicence != appId) {
            Result r = Result::fail(ExitLicence, u"%1 needs you to accept its licence (%2) before it is installed"_s.arg(m.name, m.licenceUrl));
            r.data[u"licence"_s] = QJsonObject{{u"app"_s, appId}, {u"name"_s, m.licence}, {u"url"_s, m.licenceUrl}};
            return r;
        }
        accepted = QJsonObject{{u"name"_s, m.licence}, {u"url"_s, m.licenceUrl},
                               {u"accepted_at"_s, QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                               {u"accepted_by"_s, opt.acceptedBy.isEmpty() ? qEnvironmentVariable("USER") : opt.acceptedBy}};
    }
    QJsonObject idx = readIndex();
    QJsonObject apps = idx.value(u"apps"_s).toObject();
    if (apps.contains(appId))
        return Result::fail(ExitConflict, u"%1 is already installed in the group %2"_s.arg(m.name, apps.value(appId).toObject().value(u"group"_s).toString()));
    d->stepOf = 2 + int(m.recipe.size()) + 2;
    d->step(u"Checking the %1 app group"_s.arg(group));
    bool created = false;
    if (group != QLatin1String(kDefaultGroup)) {
        bool ok = false, exists = false;
        d->containerInfo(containerFor(group), &exists, &ok);
        if (!ok)
            return Result::fail(ExitIncus, u"cannot talk to incus"_s);
        if (!exists)
            return Result::fail(ExitNotFound, u"there is no app group called %1 (create it with: taos-appgroups group create %1)"_s.arg(group));
    }
    if (Result r = runRecipe(m, group, &created); !r.ok)
        return r;
    const QString ct = containerFor(group);
    d->step(u"Adding %1 to the launcher"_s.arg(m.name));
    if (!writeLauncher(m, group, &err))
        return Result::fail(ExitFailed, err);
    QJsonObject rec{{u"group"_s, group},
                    {u"target"_s, QJsonObject{{u"kind"_s, u"group"_s}, {u"id"_s, group}}},
                    {u"installed_at"_s, QDateTime::currentDateTimeUtc().toString(Qt::ISODate)},
                    {u"manifest"_s, m.raw}};
    if (!accepted.isEmpty())
        rec[u"licence_accepted"_s] = accepted;
    const QString ver = packageVersion(d, ct, m.package);
    if (!ver.isEmpty())
        rec[u"installed_version"_s] = ver;
    if (!debSha(m).isEmpty())
        rec[u"installed_sha256"_s] = debSha(m);
    apps[appId] = rec;
    idx[u"apps"_s] = apps;
    if (!writeIndex(idx, &err))
        return Result::fail(ExitFailed, err);
    if (Result r = applyGrants(d, ct, unionGrants(group), false); !r.ok)
        return r;
    Result r;
    r.data[u"id"_s] = appId;
    r.data[u"group"_s] = group;
    r.data[u"container"_s] = ct;
    r.data[u"launcher"_s] = desktopPath(appId);
    r.data[u"desktop_id"_s] = u"taos-appgroups-"_s + appId;
    r.data[u"window_app_id"_s] = m.windowAppId;
    r.data[u"version"_s] = ver;
    return r;
}

Result Engine::remove(const QString &appId, bool purgeData, const Progress &p)
{
    d->prog = p;
    d->stepN = 0;
    d->stepOf = 4;
    QJsonObject idx = readIndex();
    QJsonObject apps = idx.value(u"apps"_s).toObject();
    if (!apps.contains(appId))
        return Result::fail(ExitNotFound, u"%1 is not installed"_s.arg(appId));
    const QJsonObject rec = apps.value(appId).toObject();
    Manifest m;
    QString err;
    if (!parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &err))
        return Result::fail(ExitRefused, err);
    const QString group = rec.value(u"group"_s).toString();
    const QString ct = containerFor(group);
    d->step(u"Removing %1 from the container"_s.arg(m.name));
    bool ok = false, exists = false;
    const QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus"_s);
    if (exists) {
        if (c.value(u"status"_s).toString() != u"Running"_s)
            d->incusRun({u"start"_s, ct}, {}, 120000);
        const QString log = dataRoot() + u"/logs/remove-"_s + appId + u".log"_s;
        QDir().mkpath(QFileInfo(log).absolutePath());
        if (Result r = uninstallRecipe(d, ct, m, group, apps, log); !r.ok)
            return r;
        if (purgeData)
            d->exec(ct, {u"rm"_s, u"-rf"_s, kHomeBase + u"/"_s + appId});
    }
    d->step(u"Removing the launcher entry"_s);
    QFile::remove(desktopPath(appId));
    QFile::remove(iconPath(appId));
    apps.remove(appId);
    idx[u"apps"_s] = apps;
    if (!writeIndex(idx, &err))
        return Result::fail(ExitFailed, err);
    if (exists)
        applyGrants(d, ct, unionGrants(group), false);
    Result r;
    r.data[u"id"_s] = appId;
    r.data[u"data_kept"_s] = !purgeData;
    return r;
}

// ------------------------------------------------------------------ move

namespace {

// tar from one container straight into another, process to process (no buffering through this program).
Result copyData(const Engine::Impl *d, const QString &fromCt, const QString &toCt, const QString &home, const QStringList &rels, const QString &appName)
{
    QProcess src, dst;
    src.setProcessEnvironment(d->penv);
    dst.setProcessEnvironment(d->penv);
    src.setStandardOutputProcess(&dst);
    dst.setProcessChannelMode(QProcess::MergedChannels);
    src.setStandardErrorFile(QProcess::nullDevice());
    dst.start(d->incus, QStringList{u"exec"_s, toCt, u"--"_s, u"tar"_s, u"-C"_s, home, u"-xpf"_s, u"-"_s, u"--numeric-owner"_s});
    src.start(d->incus, QStringList{u"exec"_s, fromCt, u"--"_s, u"tar"_s, u"-C"_s, home, u"-cf"_s, u"-"_s, u"--numeric-owner"_s} + rels);
    if (!src.waitForStarted(5000) || !dst.waitForStarted(5000))
        return Result::fail(ExitIncus, u"cannot start incus to copy %1's data"_s.arg(appName));
    src.waitForFinished(-1);
    dst.waitForFinished(-1);
    if (src.exitStatus() != QProcess::NormalExit || src.exitCode() != 0 || dst.exitStatus() != QProcess::NormalExit || dst.exitCode() != 0)
        return Result::fail(ExitStep, u"copying %1's data failed: %2"_s.arg(appName, QString::fromUtf8(dst.readAll()).trimmed().left(200)));
    return {};
}

// "<file count> <bytes>" of the given paths under the home, or empty when it could not be measured.
QString dataFingerprint(const Engine::Impl *d, const QString &ct, const QString &home, const QStringList &rels)
{
    const Proc r = d->exec(ct, QStringList{u"sh"_s, u"-c"_s, u"cd \"$1\" && shift && find \"$@\" -type f -printf '%s\\n' | awk '{n++;s+=$1} END{print n+0, s+0}'"_s, u"sh"_s, home} + rels);
    return r.ok() ? r.out.trimmed() : QString();
}

void cleanTargetHome(const Engine::Impl *d, const QString &ct, const QString &home)
{
    d->exec(ct, {u"find"_s, home, u"-mindepth"_s, u"1"_s, u"-maxdepth"_s, u"1"_s, u"-exec"_s, u"rm"_s, u"-rf"_s, u"{}"_s, u"+"_s});
}

} // namespace

Result Engine::move(const QString &appId, const QString &toGroup, const Progress &p)
{
    d->prog = p;
    d->stepN = 0;
    QJsonObject idx = readIndex();
    QJsonObject apps = idx.value(u"apps"_s).toObject();
    if (!apps.contains(appId))
        return Result::fail(ExitNotFound, u"%1 is not installed"_s.arg(appId));
    const QJsonObject rec = apps.value(appId).toObject();
    Manifest m;
    QString err;
    if (!parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &err))
        return Result::fail(ExitRefused, err);
    d->stepOf = 8;
    if (!validGroupName(toGroup))
        return Result::fail(ExitUsage, u"bad group name"_s);
    const QString from = rec.value(u"group"_s).toString();
    if (from == toGroup)
        return Result::fail(ExitConflict, u"%1 is already in the group %2"_s.arg(m.name, toGroup));
    if (m.licenceRequired && rec.value(u"licence_accepted"_s).toObject().isEmpty())
        return Result::fail(ExitLicence, u"%1 has no recorded licence acceptance; accept it again by installing it first"_s.arg(m.name));
    const QString srcCt = containerFor(from), dstCt = containerFor(toGroup);
    d->step(u"Checking %1 can be moved"_s.arg(m.name));
    bool ok = false, sExists = false, tExists = false;
    QJsonObject sc = d->containerInfo(srcCt, &sExists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus"_s);
    const QJsonObject tc = d->containerInfo(dstCt, &tExists, &ok);
    if (!tExists || !ownedGroup(tc))
        return Result::fail(ExitNotFound, u"there is no app group called %1 (create it first)"_s.arg(toGroup));
    if (!sExists || !ownedGroup(sc))
        return Result::fail(ExitNotFound, u"the app group %1 that holds %2 does not exist"_s.arg(from, m.name));
    if (sc.value(u"status"_s).toString() != u"Running"_s && !d->incusRun({u"start"_s, srcCt}, {}, 120000).ok())
        return Result::fail(ExitIncus, u"cannot start %1"_s.arg(srcCt));
    bool known = false;
    if (appRunning(d, srcCt, appId, &known))
        return Result::fail(ExitConflict, u"%1 is running. Close it and try again; nothing was changed."_s.arg(m.name));
    if (!known)
        return Result::fail(ExitIncus, u"could not tell whether %1 is running, so it was not moved; nothing was changed"_s.arg(m.name));
    bool created = false;
    if (Result r = ensureGroupImpl(d, toGroup, &created); !r.ok)
        return r;
    const QString home = kHomeBase + u"/"_s + appId;
    // A home that does not exist yet is empty (a bare find would print its complaint into the answer).
    const Proc occupied = d->exec(dstCt, {u"sh"_s, u"-c"_s, u"[ -d \"$1\" ] && find \"$1\" -mindepth 1 -maxdepth 1 -print -quit; true"_s, u"sh"_s, home});
    if (!occupied.ok())
        return Result::fail(ExitIncus, u"could not look at %1 in the group %2; nothing was changed"_s.arg(home, toGroup));
    if (!occupied.out.trimmed().isEmpty())
        return Result::fail(ExitConflict, u"the group %1 already holds data for %2; it was not overwritten and nothing was changed"_s.arg(toGroup, m.name));
    // Which of the app's data paths exist in the source: the manifest's list, or the whole home.
    const bool wholeHome = m.data.isEmpty();
    QStringList rels;
    if (wholeHome) {
        rels << u"."_s;
    } else {
        for (const QString &r : m.data)
            if (d->exec(srcCt, {u"test"_s, u"-e"_s, home + u"/"_s + r}).ok())
                rels << r;
    }
    // 1. the app itself, staged in the target (not listed, no launcher)
    d->step(u"Installing %1 in %2"_s.arg(m.name, toGroup));
    if (Result r = runRecipe(m, toGroup, &created); !r.ok)
        return r;
    // 2. its data
    d->step(u"Copying %1's data"_s.arg(m.name));
    if (!rels.isEmpty()) {
        if (Result r = copyData(d, srcCt, dstCt, home, rels, m.name); !r.ok) {
            cleanTargetHome(d, dstCt, home);
            return r;
        }
        const QString a = dataFingerprint(d, srcCt, home, rels), b = dataFingerprint(d, dstCt, home, rels);
        if (a.isEmpty() || b.isEmpty() || a != b) {
            cleanTargetHome(d, dstCt, home);
            return Result::fail(ExitStep, u"the copy of %1's data does not match the original (%2 against %3); nothing was changed"_s.arg(m.name, a.isEmpty() ? u"unknown"_s : a, b.isEmpty() ? u"unknown"_s : b));
        }
    }
    // 3. it can start there: the program is in place and every library it needs is found
    d->step(u"Checking %1 can start in %2"_s.arg(m.name, toGroup));
    const Proc ldd = d->exec(dstCt, {u"ldd"_s, m.binary});
    if (!d->exec(dstCt, {u"test"_s, u"-x"_s, m.binary}).ok() || ldd.out.contains(u"not found"_s)) {
        cleanTargetHome(d, dstCt, home);
        return Result::fail(ExitStep, u"%1 would not start in %2 (a program or library is missing); nothing was changed. %3"_s.arg(m.name, toGroup, ldd.out.trimmed().left(200)));
    }
    // 4. switch: index and launcher
    d->step(u"Switching %1 to %2"_s.arg(m.name, toGroup));
    QJsonObject newRec = rec;
    newRec[u"group"_s] = toGroup;
    newRec[u"target"_s] = QJsonObject{{u"kind"_s, u"group"_s}, {u"id"_s, toGroup}};
    newRec[u"previous_group"_s] = from;
    newRec[u"moved_at"_s] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    apps[appId] = newRec;
    idx[u"apps"_s] = apps;
    if (!writeIndex(idx, &err)) {
        cleanTargetHome(d, dstCt, home);
        return Result::fail(ExitFailed, err);
    }
    if (!writeLauncher(m, toGroup, &err)) {
        QJsonObject back = readIndex();
        QJsonObject bapps = back.value(u"apps"_s).toObject();
        bapps[appId] = rec;
        back[u"apps"_s] = bapps;
        QString e2;
        writeIndex(back, &e2);
        return Result::fail(ExitFailed, err);
    }
    applyGrants(d, dstCt, unionGrants(toGroup), false);
    // 5. the old data is renamed, never deleted
    d->step(u"Keeping a backup of the old data"_s);
    const QString stamp = QDateTime::currentDateTime().toString(u"yyyyMMdd-HHmmss"_s);
    const QString backup = kHomeBase + u"/.backup/"_s + appId + u"-"_s + stamp;
    Proc bk;
    if (wholeHome)
        bk = d->exec(srcCt, {u"sh"_s, u"-c"_s, u"mkdir -p \"$1\" && find \"$2\" -mindepth 1 -maxdepth 1 -exec mv -t \"$1\" {} +"_s, u"sh"_s, backup, home});
    else
        bk = d->exec(srcCt, QStringList{u"sh"_s, u"-c"_s, u"set -e; b=$1; h=$2; shift 2; mkdir -p \"$b\"; cd \"$h\"; for r in \"$@\"; do mkdir -p \"$b/$(dirname \"$r\")\"; mv \"$r\" \"$b/$r\"; done"_s, u"sh"_s, backup, home} + rels);
    Result r;
    r.data[u"id"_s] = appId;
    r.data[u"group"_s] = toGroup;
    r.data[u"from"_s] = from;
    r.data[u"container"_s] = dstCt;
    r.data[u"launcher"_s] = desktopPath(appId);
    if (!bk.ok()) {
        r.ok = false;
        r.exit = ExitStep;
        r.error = u"%1 now lives in %2, but its old data could not be set aside in %3 (it is still there, untouched); the old copy of the app was not removed"_s.arg(m.name, toGroup, from);
        return r;
    }
    if (!rels.isEmpty()) {
        QJsonObject cur = readIndex();
        QJsonArray bks = cur.value(u"backups"_s).toArray();
        bks.append(QJsonObject{{u"app"_s, appId}, {u"group"_s, from}, {u"stamp"_s, stamp}, {u"path"_s, backup},
                               {u"created_at"_s, QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}});
        cur[u"backups"_s] = bks;
        writeIndex(cur, &err);
        r.data[u"backup"_s] = backup;
    }
    // 6. the old copy of the app
    d->step(u"Removing the old copy of %1 from %2"_s.arg(m.name, from));
    const QString log = dataRoot() + u"/logs/move-"_s + appId + u".log"_s;
    QDir().mkpath(QFileInfo(log).absolutePath());
    if (Result u = uninstallRecipe(d, srcCt, m, from, apps, log); !u.ok) {
        r.ok = false;
        r.exit = u.exit;
        r.error = u"%1 now lives in %2, but removing the old copy from %3 failed: %4"_s.arg(m.name, toGroup, from, u.error);
        return r;
    }
    applyGrants(d, srcCt, unionGrants(from), false);
    return r;
}

Result Engine::backups()
{
    Result r;
    r.data[u"backups"_s] = readIndex().value(u"backups"_s).toArray();
    return r;
}

Result Engine::backupDelete(const QString &appId, const QString &stamp)
{
    if (!QRegularExpression(u"^[a-z][a-z0-9-]{1,40}$"_s).match(appId).hasMatch() || !QRegularExpression(u"^\\d{8}-\\d{6}$"_s).match(stamp).hasMatch())
        return Result::fail(ExitUsage, u"usage: backups delete <app-id> <yyyymmdd-hhmmss>"_s);
    QJsonObject idx = readIndex();
    QJsonArray bks = idx.value(u"backups"_s).toArray(), keep;
    QJsonObject found;
    for (const QJsonValue &v : bks) {
        const QJsonObject b = v.toObject();
        if (b.value(u"app"_s).toString() == appId && b.value(u"stamp"_s).toString() == stamp)
            found = b;
        else
            keep.append(v);
    }
    if (found.isEmpty())
        return Result::fail(ExitNotFound, u"no such backup"_s);
    const QString ct = containerFor(found.value(u"group"_s).toString());
    bool ok = false, exists = false;
    const QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus"_s);
    if (exists) {
        if (c.value(u"status"_s).toString() != u"Running"_s)
            d->incusRun({u"start"_s, ct}, {}, 120000);
        if (!d->exec(ct, {u"rm"_s, u"-rf"_s, found.value(u"path"_s).toString()}).ok())
            return Result::fail(ExitStep, u"could not delete the backup"_s);
    }
    idx[u"backups"_s] = keep;
    QString err;
    if (!writeIndex(idx, &err))
        return Result::fail(ExitFailed, err);
    Result r;
    r.data[u"deleted"_s] = found;
    return r;
}

// ------------------------------------------------------------------ update

Result Engine::update(const QString &appId, bool checkOnly, const Progress &p)
{
    d->prog = p;
    d->stepN = 0;
    d->stepOf = 4;
    QJsonObject idx = readIndex();
    QJsonObject apps = idx.value(u"apps"_s).toObject();
    if (!apps.contains(appId))
        return Result::fail(ExitNotFound, u"%1 is not installed"_s.arg(appId));
    QJsonObject rec = apps.value(appId).toObject();
    Manifest m;
    QString err;
    if (!parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &err))
        return Result::fail(ExitRefused, err);
    const bool feed = !m.updateFeedUrl.isEmpty() && hasOp(m, u"download-deb"_s);
    const bool apt = !feed && !m.package.isEmpty() && (hasOp(m, u"apt-source"_s) || hasOp(m, u"apt-install"_s));
    if (!feed && !apt)
        return Result::fail(ExitRefused, u"%1 has no update path: it is updated by a new catalogue entry"_s.arg(m.name));
    const QString ct = containerFor(rec.value(u"group"_s).toString());
    bool ok = false, exists = false;
    const QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus"_s);
    if (!exists)
        return Result::fail(ExitNotFound, u"the app group %1 does not exist"_s.arg(rec.value(u"group"_s).toString()));
    if (c.value(u"status"_s).toString() != u"Running"_s && !d->incusRun({u"start"_s, ct}, {}, 120000).ok())
        return Result::fail(ExitIncus, u"cannot start %1"_s.arg(ct));
    if (!checkOnly) {
        bool known = false;
        if (appRunning(d, ct, appId, &known))
            return Result::fail(ExitConflict, u"%1 is running. Close it and try again; nothing was changed."_s.arg(m.name));
        if (!known)
            return Result::fail(ExitIncus, u"could not tell whether %1 is running, so it was not updated"_s.arg(m.name));
    }
    const QString log = dataRoot() + u"/logs/update-"_s + appId + u".log"_s;
    QDir().mkpath(QFileInfo(log).absolutePath());
    const QStringList env = {u"DEBIAN_FRONTEND=noninteractive"_s};
    Result r;
    r.data[u"id"_s] = appId;
    const QString installedVersion = rec.value(u"installed_version"_s).toString(m.version);
    r.data[u"installed_version"_s] = installedVersion;
    if (feed) {
        d->step(u"Asking the publisher for the current version"_s);
        const Proc f = d->exec(ct, {u"curl"_s, u"-fsSL"_s, u"--retry"_s, u"3"_s, m.updateFeedUrl});
        const QJsonObject doc = QJsonDocument::fromJson(f.out.toUtf8()).object();
        const QString want = QSysInfo::currentCpuArchitecture() == u"x86_64"_s ? u"x64"_s : u"arm64"_s;
        QJsonObject entry;
        for (const QJsonValue &v : doc.value(u"downloads"_s).toArray()) {
            const QJsonObject e = v.toObject();
            if (e.value(u"platform"_s).toString() == u"linux"_s && e.value(u"arch"_s).toString() == want && e.value(u"type"_s).toString() == u"deb"_s)
                entry = e;
        }
        QString why;
        if (!f.ok() || entry.isEmpty() || !QRegularExpression(u"^[0-9a-f]{64}$"_s).match(entry.value(u"sha256"_s).toString()).hasMatch()
            || !urlAllowed(entry.value(u"url"_s).toString(), &why) || doc.value(u"version"_s).toString().isEmpty())
            return Result::fail(ExitStep, u"could not read a usable update feed from the publisher%1"_s.arg(why.isEmpty() ? QString() : u" ("_s + why + u")"_s));
        const QString sha = entry.value(u"sha256"_s).toString();
        const bool available = sha != rec.value(u"installed_sha256"_s).toString(debSha(m));
        r.data[u"latest_version"_s] = doc.value(u"version"_s).toString();
        r.data[u"update_available"_s] = available;
        if (checkOnly || !available)
            return r;
        d->step(u"Downloading %1 %2"_s.arg(m.name, doc.value(u"version"_s).toString()));
        const QString tmp = u"/var/tmp/taos-dl-"_s + appId + u".deb"_s;
        if (!d->exec(ct, {u"curl"_s, u"-fL"_s, u"--retry"_s, u"3"_s, u"-o"_s, tmp, entry.value(u"url"_s).toString()}, log).ok())
            return Result::fail(ExitStep, u"the download failed (log: %1)"_s.arg(log));
        const Proc sum = d->exec(ct, {u"sha256sum"_s, tmp});
        const QString got = sum.out.left(64);
        if (!sum.ok() || got != sha) {
            d->exec(ct, {u"rm"_s, u"-f"_s, tmp});
            return Result::fail(ExitStep, u"the download does not match the checksum in the publisher's feed (got %1); it was deleted and not installed"_s.arg(got));
        }
        d->step(u"Installing the new version"_s);
        const Proc ins = d->exec(ct, {u"apt-get"_s, u"install"_s, u"-y"_s, tmp}, log, env);
        d->exec(ct, {u"rm"_s, u"-f"_s, tmp});
        if (!ins.ok())
            return Result::fail(ExitStep, u"installing the new version failed (log: %1)"_s.arg(log));
        rec[u"installed_sha256"_s] = sha;
    } else {
        d->step(u"Asking the software source for the current version"_s);
        if (!d->exec(ct, {u"apt-get"_s, u"update"_s}, log, env).ok())
            return Result::fail(ExitStep, u"apt-get update failed (log: %1)"_s.arg(log));
        const Proc pol = d->exec(ct, {u"apt-cache"_s, u"policy"_s, m.package});
        QString inst, cand;
        for (const QString &l : pol.out.split(u'\n')) {
            const QString t = l.trimmed();
            if (t.startsWith(u"Installed:"_s)) inst = t.mid(10).trimmed();
            if (t.startsWith(u"Candidate:"_s)) cand = t.mid(10).trimmed();
        }
        if (!pol.ok() || cand.isEmpty() || cand == u"(none)"_s)
            return Result::fail(ExitStep, u"could not find out the current version of %1"_s.arg(m.package));
        const bool available = cand != inst;
        r.data[u"installed_version"_s] = inst;
        r.data[u"latest_version"_s] = cand;
        r.data[u"update_available"_s] = available;
        if (checkOnly || !available)
            return r;
        d->step(u"Installing the new version"_s);
        if (!d->exec(ct, {u"apt-get"_s, u"install"_s, u"-y"_s, u"--no-install-recommends"_s, u"--only-upgrade"_s, m.package}, log, env).ok())
            return Result::fail(ExitStep, u"the upgrade failed (log: %1)"_s.arg(log));
    }
    const QString ver = packageVersion(d, ct, m.package);
    if (!ver.isEmpty())
        rec[u"installed_version"_s] = ver;
    rec[u"updated_at"_s] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    apps[appId] = rec;
    idx[u"apps"_s] = apps;
    if (!writeIndex(idx, &err))
        return Result::fail(ExitFailed, err);
    r.data[u"installed_version"_s] = ver;
    r.data[u"updated"_s] = true;
    return r;
}

// ------------------------------------------------------------------ run

namespace {

struct Session {
    QString runtime, display, socket, hash;
};

bool findSession(Session *s)
{
    s->runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
    s->display = envOr("WAYLAND_DISPLAY", u"wayland-0"_s);
    s->socket = s->display.startsWith(u'/') ? s->display : s->runtime + u"/"_s + s->display;
    if (s->runtime.isEmpty() || !QFileInfo(s->socket).exists())
        return false;
    s->hash = QString::fromLatin1(QCryptographicHash::hash(s->socket.toUtf8(), QCryptographicHash::Sha1).toHex().left(8));
    return true;
}

} // namespace

Result Engine::run(const QString &appId, const QStringList &extra)
{
    const QJsonObject rec = readIndex().value(u"apps"_s).toObject().value(appId).toObject();
    if (rec.isEmpty())
        return Result::fail(ExitNotFound, u"%1 is not installed"_s.arg(appId));
    Manifest m;
    QString err;
    if (!parseManifest(QJsonDocument(rec.value(u"manifest"_s).toObject()).toJson(), &m, &err))
        return Result::fail(ExitRefused, err);
    Session s;
    if (!findSession(&s))
        return Result::fail(ExitNoSession, u"there is no desktop session to show %1 in (XDG_RUNTIME_DIR and WAYLAND_DISPLAY must name a Wayland socket)"_s.arg(m.name));
    const QString group = rec.value(u"group"_s).toString();
    const QString ct = containerFor(group);
    bool ok = false, exists = false;
    QJsonObject c = d->containerInfo(ct, &exists, &ok);
    if (!ok)
        return Result::fail(ExitIncus, u"cannot talk to incus"_s);
    if (!exists || !ownedGroup(c))
        return Result::fail(ExitNotFound, u"the app group %1 does not exist"_s.arg(group));
    if (c.value(u"status"_s).toString() != u"Running"_s) {
        if (!d->incusRun({u"start"_s, ct}, {}, 120000).ok())
            return Result::fail(ExitIncus, u"cannot start %1"_s.arg(ct));
        c = d->containerInfo(ct, &exists, &ok);
    }
    // Per-session sockets: the Wayland socket always, the pulse socket for apps with the audio grant. Devices of
    // sessions that no longer exist are removed first.
    const QJsonObject devs = c.value(u"devices"_s).toObject();
    for (auto it = devs.begin(); it != devs.end(); ++it) {
        if (!it.key().startsWith(u"wl-"_s) && !it.key().startsWith(u"pa-"_s))
            continue;
        const QString conn = it.value().toObject().value(u"connect"_s).toString().mid(5);
        if (!QFileInfo::exists(conn))
            removeDevice(d, ct, it.key(), &err);
    }
    const QString wl = u"wl-"_s + s.hash, pa = u"pa-"_s + s.hash;
    const QStringList common = {u"proxy"_s, u"bind=container"_s, u"uid=1000"_s, u"gid=1000"_s, u"mode=0700"_s,
                                u"security.uid=1000"_s, u"security.gid=1000"_s};
    if (!hasDevice(c, wl) && !addDevice(d, ct, wl, common + QStringList{u"connect=unix:"_s + s.socket, u"listen=unix:"_s + kSessionDir + u"/"_s + wl}, &err))
        return Result::fail(ExitIncus, u"cannot give the container the Wayland socket: %1"_s.arg(err));
    const QString pulse = s.runtime + u"/pulse/native"_s;
    const bool audio = m.grants.audio && QFileInfo::exists(pulse);
    if (audio && !hasDevice(c, pa) && !addDevice(d, ct, pa, common + QStringList{u"connect=unix:"_s + pulse, u"listen=unix:"_s + kSessionDir + u"/"_s + pa}, &err))
        return Result::fail(ExitIncus, u"cannot give the container the audio socket: %1"_s.arg(err));
    const QString home = kHomeBase + u"/"_s + appId;
    d->incusRun({u"exec"_s, ct, u"--"_s, u"install"_s, u"-d"_s, u"-o"_s, u"1000"_s, u"-g"_s, u"1000"_s, u"-m"_s, u"0700"_s, u"/run/user/1000"_s, home});
    if (m.grants.documents)
        d->incusRun({u"exec"_s, ct, u"--"_s, u"ln"_s, u"-sfn"_s, u"/mnt/taos/documents"_s, home + u"/Documents for apps"_s});
    if (m.grants.downloads)
        d->incusRun({u"exec"_s, ct, u"--"_s, u"ln"_s, u"-sfn"_s, u"/mnt/taos/downloads"_s, home + u"/Downloads"_s});
    // One session bus for the app user, shared by every launch: a second launch of an app (a link opened in a running
    // browser) must reach the first instance, and instances find each other over this bus. Already running is fine.
    d->incusRun({u"exec"_s, ct, u"--user"_s, u"1000"_s, u"--group"_s, u"1000"_s, u"--env"_s, u"XDG_RUNTIME_DIR=/run/user/1000"_s, u"--"_s, u"dbus-daemon"_s,
                 u"--session"_s, u"--address=unix:path=/run/user/1000/bus"_s, u"--fork"_s});
    QStringList a = {u"exec"_s, ct, u"--user"_s, u"1000"_s, u"--group"_s, u"1000"_s, u"--cwd"_s, home,
                     u"--env"_s, u"HOME="_s + home, u"--env"_s, u"XDG_RUNTIME_DIR=/run/user/1000"_s,
                     u"--env"_s, u"WAYLAND_DISPLAY="_s + kSessionDir + u"/"_s + wl, u"--env"_s, u"XDG_SESSION_TYPE=wayland"_s,
                     u"--env"_s, u"LANG=C.UTF-8"_s, u"--env"_s, u"XDG_CURRENT_DESKTOP=taOS"_s,
                     u"--env"_s, u"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"_s};
    if (audio)
        a << u"--env"_s << u"PULSE_SERVER=unix:"_s + kSessionDir + u"/"_s + pa;
    for (auto it = m.launchEnv.begin(); it != m.launchEnv.end(); ++it)
        a << u"--env"_s << it.key() + u"="_s + it.value().toString();
    a << u"--"_s << m.launchArgv << extra;
    QProcess p;
    p.setProcessEnvironment(d->penv);
    p.setProcessChannelMode(QProcess::ForwardedChannels);
    p.start(d->incus, a);
    if (!p.waitForStarted(5000))
        return Result::fail(ExitIncus, u"cannot start incus"_s);
    p.waitForFinished(-1);
    Result r;
    r.data[u"exit_code"_s] = p.exitCode();
    r.data[u"id"_s] = appId;
    if (p.exitStatus() != QProcess::NormalExit || p.exitCode() != 0) {
        r.ok = false;
        r.exit = p.exitCode() > 0 && p.exitCode() < 256 ? ExitFailed : ExitFailed;
        r.error = u"%1 exited with code %2"_s.arg(m.name).arg(p.exitCode());
    }
    return r;
}

Result Engine::syncLaunchers()
{
    Result r;
    QJsonArray done;
    const QJsonObject apps = readIndex().value(u"apps"_s).toObject();
    for (auto it = apps.begin(); it != apps.end(); ++it) {
        Manifest m;
        QString err;
        if (!parseManifest(QJsonDocument(it.value().toObject().value(u"manifest"_s).toObject()).toJson(), &m, &err))
            return Result::fail(ExitRefused, err);
        if (!writeLauncher(m, it.value().toObject().value(u"group"_s).toString(), &err))
            return Result::fail(ExitFailed, err);
        done.append(desktopPath(m.id));
    }
    r.data[u"launchers"_s] = done;
    return r;
}

// ------------------------------------------------------------------ status

Result Engine::status()
{
    Result r;
    r.data[u"incus_path"_s] = d->mode;
    r.data[u"isolation_between_people"_s] = d->mode == u"per-user"_s ? u"enforced (own restricted project)"_s
                                                                       : u"not enforced (admin access to the default project)"_s;
    bool ok = false;
    const QJsonArray all = d->listContainers(&ok);
    r.data[u"incus_reachable"_s] = ok;
    if (!ok) {
        r.data[u"groups"_s] = QJsonArray();
        return r;
    }
    QJsonArray out;
    const QJsonObject idx = readIndex().value(u"apps"_s).toObject();
    for (const QJsonValue &v : all) {
        const QJsonObject c = v.toObject();
        if (!ownedGroup(c))
            continue;
        const QString g = c.value(u"config"_s).toObject().value(u"user.taos.group"_s).toString();
        const Grants gr = unionGrants(g);
        QJsonObject devs;
        for (auto it = c.value(u"devices"_s).toObject().begin(); it != c.value(u"devices"_s).toObject().end(); ++it)
            devs[it.key()] = it.value().toObject().value(u"type"_s);
        QJsonObject grants{{u"network"_s, gr.network}, {u"gpu"_s, gr.gpu}, {u"audio"_s, gr.audio}, {u"documents"_s, gr.documents},
                           {u"downloads"_s, gr.downloads}, {u"nesting"_s, gr.nesting}};
        QJsonArray apps;
        for (auto it = idx.begin(); it != idx.end(); ++it)
            if (it.value().toObject().value(u"group"_s).toString() == g)
                apps.append(it.key());
        out.append(QJsonObject{{u"name"_s, g}, {u"container"_s, c.value(u"name"_s)}, {u"state"_s, c.value(u"status"_s).toString().toLower()},
                               {u"privileged"_s, c.value(u"config"_s).toObject().value(u"security.privileged"_s).toString() == u"true"_s},
                               {u"grants"_s, grants}, {u"devices"_s, devs}, {u"apps"_s, apps}});
    }
    r.data[u"groups"_s] = out;
    return r;
}

} // namespace TaosAppGroups
