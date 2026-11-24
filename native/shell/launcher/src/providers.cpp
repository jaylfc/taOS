#include "providers.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocale>
#include <QProcess>
#include <QStandardPaths>
#include <QThread>
#include <algorithm>

#include "calc.h"
#include "fuzzy.h"

namespace taos::search {

bool executableExists(const QString &name) { return !QStandardPaths::findExecutable(name).isEmpty(); }

// ---------------------------------------------------------------- apps

AppsProvider::AppsProvider(std::shared_ptr<Frecency> frecency, QStringList dirs, QString locale, QStringList desktops)
    : m_frec(std::move(frecency)), m_dirs(std::move(dirs)), m_desktops(std::move(desktops)), m_locale(std::move(locale)) {
    if (m_dirs.isEmpty()) m_dirs = applicationDirs();
    if (m_locale.isEmpty()) m_locale = qEnvironmentVariable("LC_MESSAGES", qEnvironmentVariable("LANG", QStringLiteral("C")));
    if (m_desktops.isEmpty())
        for (const QString &d : qEnvironmentVariable("XDG_CURRENT_DESKTOP").split(QLatin1Char(':'), Qt::SkipEmptyParts)) m_desktops << d;
}

qint64 AppsProvider::dirsStamp() const {
    qint64 s = 0;
    for (const QString &d : m_dirs) s = qMax(s, QFileInfo(d).lastModified().toMSecsSinceEpoch());
    return s;
}

void AppsProvider::reload() {
    const QList<DesktopEntry> e = scanDesktopEntries(m_dirs, m_locale, m_desktops);
    QMutexLocker l(&m_mu);
    m_entries = e;
    m_loaded = true;
    m_stamp = dirsStamp();
}

void AppsProvider::prepare() {
    // cheap: stat the folders; rescan (on the caller's thread, a few ms) only when something was installed
    bool need;
    { QMutexLocker l(&m_mu); need = !m_loaded || dirsStamp() != m_stamp; }
    if (need) reload();
}

void AppsProvider::ensureLoaded() {
    bool need;
    { QMutexLocker l(&m_mu); need = !m_loaded; }
    if (need) reload();
}

QList<DesktopEntry> AppsProvider::entries() {
    ensureLoaded();
    QMutexLocker l(&m_mu);
    return m_entries;
}

int AppsProvider::entryCount() const { QMutexLocker l(&m_mu); return m_entries.size(); }

QString AppsProvider::iconForAppId(const QString &appId) const {
    if (appId.isEmpty()) return {};
    const QString a = appId.toLower();
    QMutexLocker l(&m_mu);
    for (const DesktopEntry &e : m_entries) {
        QString base = e.id;
        base.chop(8);   // ".desktop"
        if (base.toLower() == a || base.section(QLatin1Char('.'), -1).toLower() == a) return e.icon;
    }
    return {};
}

Result AppsProvider::makeResult(const DesktopEntry &e, double score) const {
    Result r;
    r.id = QStringLiteral("app:") + e.id;
    r.provider = id();
    r.kind = QStringLiteral("app");
    r.title = e.name;
    r.subtitle = !e.genericName.isEmpty() ? e.genericName : e.comment;
    r.icon = e.icon;
    r.score = score;
    const QStringList argv = execToArgv(e.exec, e);
    const QString term = e.terminal ? QStringLiteral("terminal") : QString();
    r.actions << Action{QStringLiteral("open"), QStringLiteral("Open"), QStringLiteral("launch"), argv, term};
    for (const DesktopAction &a : e.actions) {
        const QStringList av = execToArgv(a.exec, e);
        if (av.isEmpty()) continue;
        r.actions << Action{QStringLiteral("action:") + a.id, a.name, QStringLiteral("launch"), av, term};
    }
    // "Keep in Dock" only when the dock tool exists. Contract assumed: `taos-dock pin <desktop-id>`.
    if (executableExists(QStringLiteral("taos-dock")))
        r.actions << Action{QStringLiteral("dock-pin"), QStringLiteral("Keep in Dock"), QStringLiteral("exec"),
                            {QStringLiteral("taos-dock"), QStringLiteral("pin"), e.id}, {}};
    return r;
}

QList<Result> AppsProvider::query(const QString &text, const CancelToken &cancel) {
    ensureLoaded();
    QList<DesktopEntry> entries;
    { QMutexLocker l(&m_mu); entries = m_entries; }
    QList<Result> out;
    const QString q = text.trimmed();
    if (q.isEmpty()) {
        for (const QString &fid : m_frec->top(QStringLiteral("app:"), 8)) {
            for (const DesktopEntry &e : entries)
                if (QStringLiteral("app:") + e.id == fid) { out << makeResult(e, 0.5 + 0.5 * m_frec->boost(fid)); break; }
        }
        return out;
    }
    for (const DesktopEntry &e : entries) {
        if (cancel.cancelled()) return {};
        double s = fuzzyScore(q, e.name);
        s = qMax(s, fuzzyScore(q, e.genericName) * 0.75);
        for (const QString &k : e.keywords) s = qMax(s, fuzzyScore(q, k) * 0.7);
        QString bin = e.exec.section(QLatin1Char(' '), 0, 0);
        bin = QFileInfo(bin).fileName();
        s = qMax(s, fuzzyScore(q, bin) * 0.6);
        if (s < 0.14) continue;
        const QString rid = QStringLiteral("app:") + e.id;
        s += 0.16 * m_frec->boost(rid);
        out << makeResult(e, s);
    }
    std::sort(out.begin(), out.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
    if (out.size() > 12) out = out.mid(0, 12);
    return out;
}

// ------------------------------------------------------------- windows

static QByteArray runWindowList(bool *ok) {
    *ok = false;
    const QString exe = QStandardPaths::findExecutable(QStringLiteral("taos-window"));
    if (exe.isEmpty()) return {};
    QProcess p;
    p.start(exe, { QStringLiteral("list"), QStringLiteral("--json") });
    if (!p.waitForFinished(350) || p.exitStatus() != QProcess::NormalExit || p.exitCode() != 0) {
        if (p.state() != QProcess::NotRunning) { p.kill(); p.waitForFinished(50); }
        return {};
    }
    *ok = true;
    return p.readAllStandardOutput();
}

WindowsProvider::WindowsProvider(Fetch fetch, std::function<QString(const QString &)> iconFor)
    : m_fetch(std::move(fetch)), m_defaultFetch(!m_fetch), m_iconFor(std::move(iconFor)) {
    if (!m_fetch) m_fetch = runWindowList;
}

bool WindowsProvider::available() const { return !m_defaultFetch || executableExists(QStringLiteral("taos-window")); }

void WindowsProvider::prepare() {
    QMutexLocker l(&m_mu);
    m_at = 0;   // the next query refetches: the list changes between launcher openings
}

QByteArray WindowsProvider::cached(bool *ok) {
    {
        QMutexLocker l(&m_mu);
        if (m_at && QDateTime::currentMSecsSinceEpoch() - m_at < 1500) { *ok = true; return m_cache; }
    }
    const QByteArray b = m_fetch(ok);   // outside the lock: a slow CLI must not block other threads' reads
    if (*ok) {
        QMutexLocker l(&m_mu);
        m_cache = b;
        m_at = QDateTime::currentMSecsSinceEpoch();
    }
    return b;
}

QList<Result> WindowsProvider::parse(const QByteArray &json, const QString &text, const std::function<QString(const QString &)> &iconFor) {
    QList<Result> out;
    const QJsonDocument d = QJsonDocument::fromJson(json);
    if (!d.isArray()) return out;
    const QString q = text.trimmed();
    int order = 0;
    for (const QJsonValue &v : d.array()) {
        if (!v.isObject()) continue;
        const QJsonObject o = v.toObject();
        const QString wid = o.value(QStringLiteral("id")).isString() ? o.value(QStringLiteral("id")).toString()
                                                                       : QString::number(qint64(o.value(QStringLiteral("id")).toDouble(-1)));
        if (wid.isEmpty() || wid == QLatin1String("-1")) continue;
        const QString title = o.value(QStringLiteral("title")).toString();
        const QString app = o.value(QStringLiteral("app_id")).toString();
        double s;
        if (q.isEmpty()) {
            if (o.value(QStringLiteral("focused")).toBool()) { ++order; continue; }   // you are already there
            s = 0.5 - 0.02 * order;
        } else {
            s = qMax(fuzzyScore(q, title), fuzzyScore(q, app) * 0.8);
            if (s < 0.14) { ++order; continue; }
            if (o.value(QStringLiteral("minimised")).toBool()) s -= 0.02;
        }
        ++order;
        Result r;
        r.id = QStringLiteral("window:") + wid;
        r.provider = QStringLiteral("windows");
        r.kind = QStringLiteral("window");
        r.title = title.isEmpty() ? (app.isEmpty() ? QStringLiteral("Window") : app) : title;
        QStringList sub;
        if (!app.isEmpty()) sub << app;
        const QJsonValue ws = o.value(QStringLiteral("workspace"));
        if (!ws.isUndefined() && !ws.isNull()) sub << QStringLiteral("Workspace %1").arg(ws.isString() ? ws.toString() : QString::number(ws.toInt()));
        if (o.value(QStringLiteral("minimised")).toBool()) sub << QStringLiteral("Minimised");
        r.subtitle = sub.join(QStringLiteral("  ·  "));
        r.icon = iconFor ? iconFor(app) : QString();
        if (r.icon.isEmpty()) r.icon = app;
        r.score = s;
        r.actions << Action{QStringLiteral("switch"), QStringLiteral("Switch"), QStringLiteral("focus-window"), {}, wid};
        out << r;
    }
    std::sort(out.begin(), out.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
    return out;
}

QList<Result> WindowsProvider::query(const QString &text, const CancelToken &cancel) {
    bool ok = false;
    const QByteArray b = cached(&ok);
    if (!ok || cancel.cancelled()) return {};
    return parse(b, text, m_iconFor);
}

// ------------------------------------------------------------ commands

namespace {
struct Cmd {
    QString id, title, subtitle, icon, verbLabel;
    QStringList keywords;
    QString needs;            // executable that must exist
    QStringList argv;
    bool confirm = false;
    QString verb = QStringLiteral("exec");
    QString data;
    QStringList orNeeds;      // alternative executables (any one is enough), argv then replaced by altArgv[i]
    QList<QStringList> altArgv;
};

QList<Cmd> catalog() {
    const auto cmd = [](const QString &id, const QString &title, const QString &sub, const QString &icon, const QStringList &kw,
                        const QString &needs, const QStringList &argv, bool confirm = false) {
        Cmd c; c.id = id; c.title = title; c.subtitle = sub; c.icon = icon; c.keywords = kw; c.needs = needs; c.argv = argv; c.confirm = confirm;
        return c;
    };
    QList<Cmd> v;
    // Session. Lock prefers swaylock, else asks logind to lock the session.
    { Cmd c = cmd(QStringLiteral("lock"), QStringLiteral("Lock"), QStringLiteral("Lock the screen"), QStringLiteral("system-lock-screen"),
                  {QStringLiteral("lock screen"), QStringLiteral("away")}, QStringLiteral("swaylock"), {QStringLiteral("swaylock"), QStringLiteral("-f")});
      c.orNeeds = {QStringLiteral("loginctl")}; c.altArgv = {{QStringLiteral("loginctl"), QStringLiteral("lock-session")}}; v << c; }
    v << cmd(QStringLiteral("logout"), QStringLiteral("Log out"), QStringLiteral("End this session"), QStringLiteral("system-log-out"),
             {QStringLiteral("sign out"), QStringLiteral("exit")}, QStringLiteral("swaymsg"), {QStringLiteral("swaymsg"), QStringLiteral("exit")}, true);
    v << cmd(QStringLiteral("reboot"), QStringLiteral("Restart"), QStringLiteral("Restart this computer"), QStringLiteral("system-reboot"),
             {QStringLiteral("reboot")}, QStringLiteral("systemctl"), {QStringLiteral("systemctl"), QStringLiteral("reboot")}, true);
    v << cmd(QStringLiteral("poweroff"), QStringLiteral("Shut down"), QStringLiteral("Turn this computer off"), QStringLiteral("system-shutdown"),
             {QStringLiteral("power off"), QStringLiteral("turn off")}, QStringLiteral("systemctl"), {QStringLiteral("systemctl"), QStringLiteral("poweroff")}, true);
    v << cmd(QStringLiteral("suspend"), QStringLiteral("Sleep"), QStringLiteral("Suspend to memory"), QStringLiteral("system-suspend"),
             {QStringLiteral("suspend")}, QStringLiteral("systemctl"), {QStringLiteral("systemctl"), QStringLiteral("suspend")});
    // Appearance, through taos-theme (ids are the shipped themes).
    const QString th = QStringLiteral("taos-theme");
    v << cmd(QStringLiteral("theme-dark"), QStringLiteral("Dark mode"), QStringLiteral("Switch the appearance to dark"), QStringLiteral("weather-clear-night"),
             {QStringLiteral("theme"), QStringLiteral("appearance"), QStringLiteral("night")}, th, {th, QStringLiteral("set"), QStringLiteral("--mode"), QStringLiteral("dark")});
    v << cmd(QStringLiteral("theme-light"), QStringLiteral("Light mode"), QStringLiteral("Switch the appearance to light"), QStringLiteral("weather-clear"),
             {QStringLiteral("theme"), QStringLiteral("appearance"), QStringLiteral("day")}, th, {th, QStringLiteral("set"), QStringLiteral("--mode"), QStringLiteral("light")});
    v << cmd(QStringLiteral("theme-auto"), QStringLiteral("Automatic light and dark"), QStringLiteral("Follow the time of day"), QStringLiteral("preferences-desktop-theme"),
             {QStringLiteral("theme"), QStringLiteral("appearance"), QStringLiteral("auto")}, th, {th, QStringLiteral("set"), QStringLiteral("--mode"), QStringLiteral("auto")});
    const QList<QPair<QString, QString>> themes = { {QStringLiteral("graphite"), QStringLiteral("Graphite")}, {QStringLiteral("graphite-ice"), QStringLiteral("Graphite Ice")},
                                                    {QStringLiteral("graphite-warm"), QStringLiteral("Graphite Warm")} };
    for (const auto &t : themes)
        v << cmd(QStringLiteral("theme-") + t.first, QStringLiteral("Theme: ") + t.second, QStringLiteral("Change the colour theme"), QStringLiteral("preferences-desktop-theme"),
                 {QStringLiteral("theme"), QStringLiteral("colour"), QStringLiteral("color")}, th, {th, QStringLiteral("set"), t.first});
    // Dock, through `taos-dock set position <bottom|left|right>` and `taos-dock set autohide <true|false>`.
    const QString dk = QStringLiteral("taos-dock");
    for (const QString &p : { QStringLiteral("bottom"), QStringLiteral("left"), QStringLiteral("right") })
        v << cmd(QStringLiteral("dock-") + p, QStringLiteral("Dock: ") + p.at(0).toUpper() + p.mid(1), QStringLiteral("Move the Dock to the %1 edge").arg(p), QStringLiteral("view-app-grid"),
                 {QStringLiteral("dock position")}, dk, {dk, QStringLiteral("set"), QStringLiteral("position"), p});
    v << cmd(QStringLiteral("dock-autohide-on"), QStringLiteral("Dock: Hide automatically"), QStringLiteral("Hide the Dock until the pointer reaches its edge"), QStringLiteral("view-app-grid"),
             {QStringLiteral("dock auto hide"), QStringLiteral("autohide")}, dk, {dk, QStringLiteral("set"), QStringLiteral("autohide"), QStringLiteral("true")});
    v << cmd(QStringLiteral("dock-autohide-off"), QStringLiteral("Dock: Always show"), QStringLiteral("Keep the Dock visible"), QStringLiteral("view-app-grid"),
             {QStringLiteral("dock auto hide"), QStringLiteral("autohide")}, dk, {dk, QStringLiteral("set"), QStringLiteral("autohide"), QStringLiteral("false")});
    // Display scale on the focused output (the runner resolves the output). Uses `taos-display scale OUTPUT PERCENT`.
    for (int pct : { 100, 125, 150, 175, 200 }) {
        Cmd c = cmd(QStringLiteral("scale-%1").arg(pct), QStringLiteral("Display scale: %1%").arg(pct), QStringLiteral("Scale this screen to %1 percent").arg(pct),
                    QStringLiteral("video-display"), {QStringLiteral("display scale"), QStringLiteral("resolution"), QStringLiteral("zoom"), QStringLiteral("size")},
                    QStringLiteral("taos-display"), {});
        c.verb = QStringLiteral("display-scale");
        c.data = QString::number(pct);
        v << c;
    }
    // Window management mode, through `taos-window mode <tiling|desktop>` (the focused workspace).
    const QString tw = QStringLiteral("taos-window");
    v << cmd(QStringLiteral("mode-tiling"), QStringLiteral("Tiling"), QStringLiteral("Arrange windows as tiles"), QStringLiteral("view-grid"),
             {QStringLiteral("tile"), QStringLiteral("window mode"), QStringLiteral("layout")}, tw, {tw, QStringLiteral("mode"), QStringLiteral("tiling")});
    v << cmd(QStringLiteral("mode-desktop"), QStringLiteral("Desktop"), QStringLiteral("Free floating windows"), QStringLiteral("video-display"),
             {QStringLiteral("floating"), QStringLiteral("window mode"), QStringLiteral("layout")}, tw, {tw, QStringLiteral("mode"), QStringLiteral("desktop")});
    return v;
}
}  // namespace

CommandsProvider::CommandsProvider(std::function<bool(const QString &)> exists, std::shared_ptr<Frecency> frecency)
    : m_exists(exists ? std::move(exists) : std::function<bool(const QString &)>(executableExists)), m_frec(std::move(frecency)) {}

static Result commandResult(const Cmd &c, const QStringList &argv, double score) {
    Result r;
    r.id = QStringLiteral("cmd:") + c.id;
    r.provider = QStringLiteral("commands");
    r.kind = QStringLiteral("command");
    r.title = c.title;
    r.subtitle = c.subtitle;
    r.icon = c.icon;
    r.score = score;
    Action a;
    a.id = QStringLiteral("run");
    a.label = c.confirm ? QStringLiteral("Confirm") : QStringLiteral("Run");
    a.verb = c.verb;
    a.args = argv;
    a.data = c.data;
    a.confirm = c.confirm;
    r.actions << a;
    return r;
}

static bool resolveArgv(const Cmd &c, const std::function<bool(const QString &)> &exists, QStringList *argv) {
    if (exists(c.needs)) { *argv = c.argv; return true; }
    for (int i = 0; i < c.orNeeds.size(); ++i)
        if (exists(c.orNeeds[i])) { *argv = c.altArgv.value(i); return true; }
    return false;
}

QList<Result> CommandsProvider::offered() const {
    QList<Result> out;
    for (const Cmd &c : catalog()) {
        QStringList argv;
        if (resolveArgv(c, m_exists, &argv)) out << commandResult(c, argv, 0);
    }
    return out;
}

QList<Result> CommandsProvider::query(const QString &text, const CancelToken &cancel) {
    const QString q = text.trimmed();
    QList<Result> out;
    if (q.isEmpty()) return out;
    for (const Cmd &c : catalog()) {
        if (cancel.cancelled()) return {};
        double s = fuzzyScore(q, c.title);
        for (const QString &k : c.keywords) s = qMax(s, fuzzyScore(q, k) * 0.7);
        if (s < 0.2) continue;
        QStringList argv;
        if (!resolveArgv(c, m_exists, &argv)) continue;
        const QString rid = QStringLiteral("cmd:") + c.id;
        if (m_frec) s += 0.1 * m_frec->boost(rid);
        out << commandResult(c, argv, s);
    }
    std::sort(out.begin(), out.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
    if (out.size() > 8) out = out.mid(0, 8);
    return out;
}

// ---------------------------------------------------------------- calc

QList<Result> CalcProvider::query(const QString &text, const CancelToken &) {
    const QString q = text.trimmed();
    if (q.isEmpty()) return {};
    const CalcResult c = evaluate(q);
    if (!c.ok || !c.isExpression) return {};
    Result r;
    r.id = QStringLiteral("calc:result");
    r.provider = id();
    r.kind = QStringLiteral("calc");
    r.title = c.text;
    r.subtitle = QStringLiteral("= %1").arg(q.startsWith(QLatin1Char('=')) ? q.mid(1).trimmed() : q);
    r.icon = QStringLiteral("accessories-calculator");
    r.score = 1.0;
    r.actions << Action{QStringLiteral("copy"), QStringLiteral("Copy"), QStringLiteral("copy"), {}, c.text};
    return {r};
}

}  // namespace taos::search
