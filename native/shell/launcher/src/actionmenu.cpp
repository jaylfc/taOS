// SPDX-License-Identifier: AGPL-3.0-or-later
#include "actionmenu.h"

#include <QJsonArray>
#include <algorithm>

#include "fuzzy.h"
#include "providers.h"

namespace taos::search {

namespace {
struct Node {
    ActionNode n;
    QList<Node> kids;
};
}  // namespace

struct ActionMenu::Impl {
    std::function<bool(const QString &)> exists;
    QList<Node> root;
};

static Node leaf(const QString &id, const QString &title, const QString &sub, const QString &icon, const QStringList &kw, Action a) {
    Node n;
    n.n.id = id; n.n.title = title; n.n.subtitle = sub; n.n.icon = icon; n.n.keywords = kw; n.n.action = std::move(a);
    return n;
}
static Action exec(const QStringList &argv, const QString &label = QStringLiteral("Run"), bool confirm = false) {
    Action a; a.id = QStringLiteral("run"); a.label = confirm ? QStringLiteral("Confirm") : label; a.verb = QStringLiteral("exec"); a.args = argv; a.confirm = confirm;
    return a;
}
static Action verb(const QString &v, const QString &data, const QString &label = QStringLiteral("Run")) {
    Action a; a.id = QStringLiteral("run"); a.label = label; a.verb = v; a.data = data;
    return a;
}

ActionMenu::ActionMenu(std::function<bool(const QString &)> exists) : d(std::make_shared<Impl>()) {
    d->exists = exists ? std::move(exists) : std::function<bool(const QString &)>(executableExists);
    const auto have = d->exists;
    const auto group = [](const QString &id, const QString &title, const QString &sub, const QString &icon, const QStringList &kw, QList<Node> kids) {
        Node g;
        g.n.id = id; g.n.title = title; g.n.subtitle = sub; g.n.icon = icon; g.n.keywords = kw; g.n.isGroup = true;
        g.kids = std::move(kids);
        return g;
    };
    // The session commands and the themes come from the one catalogue the search uses, so there is one definition of
    // "restart" and its confirm step.
    CommandsProvider cmds(d->exists);
    QHash<QString, Result> byId;
    for (const Result &r : cmds.offered()) byId.insert(r.id, r);
    const auto fromCmd = [&](const QString &cid, const QString &id, const QString &title, const QString &sub, const QString &icon, const QStringList &kw) {
        const Result r = byId.value(QStringLiteral("cmd:") + cid);
        QList<Node> o;
        if (!r.id.isEmpty()) o << leaf(id, title, sub, icon, kw, r.actions.first());
        return o;
    };
    const auto append = [](QList<Node> &to, const QList<Node> &from) { to << from; };

    // Capture
    QList<Node> cap;
    if (have(QStringLiteral("grim"))) {
        cap << leaf(QStringLiteral("capture/screen"), QStringLiteral("Screenshot of the screen"), QStringLiteral("Saved in Pictures/Screenshots"), QStringLiteral("applets-screenshooter"),
                    { QStringLiteral("capture"), QStringLiteral("screen shot") }, verb(QStringLiteral("capture-screen"), QString(), QStringLiteral("Capture")));
        if (have(QStringLiteral("slurp")))
            cap << leaf(QStringLiteral("capture/region"), QStringLiteral("Screenshot of an area"), QStringLiteral("Drag a rectangle; saved in Pictures/Screenshots"), QStringLiteral("edit-select-all"),
                        { QStringLiteral("capture"), QStringLiteral("region"), QStringLiteral("crop") }, verb(QStringLiteral("capture-region"), QString(), QStringLiteral("Capture")));
    }
    // Toggle
    QList<Node> tog;
    if (have(QStringLiteral("nmcli"))) {
        tog << leaf(QStringLiteral("toggle/wifi-on"), QStringLiteral("Wi-Fi on"), QStringLiteral("Turn the wireless radio on"), QStringLiteral("network-wireless"), { QStringLiteral("wifi"), QStringLiteral("wireless") },
                    exec({ QStringLiteral("nmcli"), QStringLiteral("radio"), QStringLiteral("wifi"), QStringLiteral("on") }));
        tog << leaf(QStringLiteral("toggle/wifi-off"), QStringLiteral("Wi-Fi off"), QStringLiteral("Turn the wireless radio off"), QStringLiteral("network-wireless-disabled"), { QStringLiteral("wifi"), QStringLiteral("wireless") },
                    exec({ QStringLiteral("nmcli"), QStringLiteral("radio"), QStringLiteral("wifi"), QStringLiteral("off") }));
    }
    if (have(QStringLiteral("bluetoothctl"))) {
        tog << leaf(QStringLiteral("toggle/bluetooth-on"), QStringLiteral("Bluetooth on"), QStringLiteral("Power the adapter"), QStringLiteral("bluetooth"), { QStringLiteral("bt") },
                    exec({ QStringLiteral("bluetoothctl"), QStringLiteral("power"), QStringLiteral("on") }));
        tog << leaf(QStringLiteral("toggle/bluetooth-off"), QStringLiteral("Bluetooth off"), QStringLiteral("Switch the adapter off"), QStringLiteral("bluetooth-disabled"), { QStringLiteral("bt") },
                    exec({ QStringLiteral("bluetoothctl"), QStringLiteral("power"), QStringLiteral("off") }));
    }
    // Style: modes and the shipped themes, through taos-theme
    QList<Node> sty;
    append(sty, fromCmd(QStringLiteral("theme-light"), QStringLiteral("style/light"), QStringLiteral("Light"), QStringLiteral("Light appearance"), QStringLiteral("weather-clear"), { QStringLiteral("day"), QStringLiteral("mode") }));
    append(sty, fromCmd(QStringLiteral("theme-dark"), QStringLiteral("style/dark"), QStringLiteral("Dark"), QStringLiteral("Dark appearance"), QStringLiteral("weather-clear-night"), { QStringLiteral("night"), QStringLiteral("mode") }));
    append(sty, fromCmd(QStringLiteral("theme-auto"), QStringLiteral("style/auto"), QStringLiteral("Automatic"), QStringLiteral("Follow the time of day"), QStringLiteral("preferences-desktop-theme"), { QStringLiteral("mode") }));
    if (have(QStringLiteral("taos-theme")))
        for (const auto &t : QList<QPair<QString, QString>>{ { "graphite", "Graphite" }, { "workbench", "Workbench" }, { "parallax", "Parallax" } })
            sty << leaf(QStringLiteral("style/theme-") + t.first, QStringLiteral("Theme: ") + t.second, QStringLiteral("Change the whole look"), QStringLiteral("preferences-desktop-theme"),
                        { QStringLiteral("theme"), QStringLiteral("style"), QStringLiteral("look") }, exec({ QStringLiteral("taos-theme"), QStringLiteral("set"), t.first }));
    // Setup: the Settings pages that exist
    QList<Node> set;
    if (have(QStringLiteral("taos-settings"))) {
        const struct { const char *sec, *title, *sub, *icon, *kw; } pages[] = {
            { "display", "Displays", "Resolution, scale, arrangement", "video-display", "screen monitor scale" },
            { "network", "Network and Bluetooth", "Wi-Fi, wired, devices", "network-wireless", "wifi ethernet bluetooth" },
            { "sound", "Sound", "Volume and devices", "audio-volume-high", "audio speaker microphone" },
            { "dock", "Dock", "Position, size, auto-hide", "view-app-grid", "taskbar" },
            { "desktop", "Desktop and windows", "Window style and shortcuts", "preferences-system-windows", "tiling floating keys" },
            { "appearance", "Appearance", "Theme and accent", "preferences-desktop-theme", "colour look" },
        };
        for (const auto &p : pages)
            set << leaf(QStringLiteral("setup/") + QString::fromLatin1(p.sec), QString::fromUtf8(p.title), QString::fromUtf8(p.sub), QString::fromLatin1(p.icon),
                        QString::fromLatin1(p.kw).split(QLatin1Char(' ')), exec({ QStringLiteral("taos-settings"), QStringLiteral("--section"), QString::fromLatin1(p.sec) }, QStringLiteral("Open")));
    }
    // Install: the Store
    QList<Node> ins;
    if (have(QStringLiteral("taos-store"))) {
        ins << leaf(QStringLiteral("install/store"), QStringLiteral("Store"), QStringLiteral("Apps, models and themes"), QStringLiteral("my.taos.store"), { QStringLiteral("apps"), QStringLiteral("get") }, exec({ QStringLiteral("taos-store") }, QStringLiteral("Open")));
        ins << leaf(QStringLiteral("install/webapps"), QStringLiteral("Web apps"), QStringLiteral("Websites as their own apps"), QStringLiteral("my.taos.store"), { QStringLiteral("website"), QStringLiteral("pwa") },
                    exec({ QStringLiteral("taos-store"), QStringLiteral("--section"), QStringLiteral("webapps") }, QStringLiteral("Open")));
        ins << leaf(QStringLiteral("install/website"), QStringLiteral("Add a website"), QStringLiteral("Make a website an app"), QStringLiteral("my.taos.store"), { QStringLiteral("web app"), QStringLiteral("url") },
                    exec({ QStringLiteral("taos-store"), QStringLiteral("--add-website") }, QStringLiteral("Open")));
    }
    // Help: the shortcuts overlay of the overview (it also lists this menu's own keys)
    QList<Node> help;
    if (have(QStringLiteral("taos-overview")))
        help << leaf(QStringLiteral("help/shortcuts"), QStringLiteral("Keyboard shortcuts"), QStringLiteral("Every key and gesture, including this menu's"), QStringLiteral("preferences-desktop-keyboard-shortcuts"),
                     { QStringLiteral("keys"), QStringLiteral("hotkeys"), QStringLiteral("help"), QStringLiteral("gestures") }, exec({ QStringLiteral("taos-overview"), QStringLiteral("help") }, QStringLiteral("Show")));
    // System
    QList<Node> sys;
    append(sys, fromCmd(QStringLiteral("lock"), QStringLiteral("system/lock"), QStringLiteral("Lock"), QStringLiteral("Lock the screen"), QStringLiteral("system-lock-screen"), { QStringLiteral("away") }));
    append(sys, fromCmd(QStringLiteral("suspend"), QStringLiteral("system/sleep"), QStringLiteral("Sleep"), QStringLiteral("Suspend to memory"), QStringLiteral("system-suspend"), {}));
    append(sys, fromCmd(QStringLiteral("logout"), QStringLiteral("system/logout"), QStringLiteral("Sign out"), QStringLiteral("End this session"), QStringLiteral("system-log-out"), { QStringLiteral("log out") }));
    append(sys, fromCmd(QStringLiteral("reboot"), QStringLiteral("system/restart"), QStringLiteral("Restart"), QStringLiteral("Restart this computer"), QStringLiteral("system-reboot"), { QStringLiteral("reboot") }));
    append(sys, fromCmd(QStringLiteral("poweroff"), QStringLiteral("system/shutdown"), QStringLiteral("Shut down"), QStringLiteral("Turn this computer off"), QStringLiteral("system-shutdown"), { QStringLiteral("power off") }));

    const auto addGroup = [&](Node g) { if (!g.kids.isEmpty()) d->root << g; };
    addGroup(group(QStringLiteral("capture"), QStringLiteral("Capture"), QStringLiteral("Screenshots"), QStringLiteral("applets-screenshooter"), { QStringLiteral("screenshot") }, cap));
    addGroup(group(QStringLiteral("toggle"), QStringLiteral("Toggle"), QStringLiteral("Wi-Fi and Bluetooth"), QStringLiteral("preferences-system-network"), { QStringLiteral("switch") }, tog));
    addGroup(group(QStringLiteral("style"), QStringLiteral("Style"), QStringLiteral("Light, dark and themes"), QStringLiteral("preferences-desktop-theme"), { QStringLiteral("appearance") }, sty));
    addGroup(group(QStringLiteral("setup"), QStringLiteral("Setup"), QStringLiteral("Displays, network, sound, dock"), QStringLiteral("preferences-system"), { QStringLiteral("settings") }, set));
    addGroup(group(QStringLiteral("install"), QStringLiteral("Install"), QStringLiteral("The Store"), QStringLiteral("my.taos.store"), { QStringLiteral("get"), QStringLiteral("add") }, ins));
    addGroup(group(QStringLiteral("help"), QStringLiteral("Help"), QStringLiteral("Keyboard shortcuts"), QStringLiteral("help-browser"), { QStringLiteral("keys") }, help));
    addGroup(group(QStringLiteral("system"), QStringLiteral("System"), QStringLiteral("Lock, sign out, restart, shut down"), QStringLiteral("system-shutdown"), { QStringLiteral("power") }, sys));
}

static const QList<Node> *kidsAt(const QList<Node> &root, const QStringList &path) {
    const QList<Node> *cur = &root;
    QString built;
    for (const QString &seg : path) {
        built = built.isEmpty() ? seg : built + QLatin1Char('/') + seg;
        const Node *hit = nullptr;
        for (const Node &n : *cur) if (n.n.id == built && n.n.isGroup) { hit = &n; break; }
        if (!hit) return nullptr;
        cur = &hit->kids;
    }
    return cur;
}

QList<ActionNode> ActionMenu::level(const QStringList &path) const {
    QList<ActionNode> out;
    const QList<Node> *k = kidsAt(d->root, path);
    if (!k) return out;
    for (const Node &n : *k) { ActionNode a = n.n; a.childCount = int(n.kids.size()); out << a; }
    return out;
}

QList<ActionNode> ActionMenu::filter(const QStringList &path, const QString &text) const {
    const QString q = text.trimmed();
    const QList<ActionNode> all = level(path);
    if (q.isEmpty()) return all;
    QList<QPair<double, ActionNode>> scored;
    for (const ActionNode &n : all) {
        double s = qMax(fuzzyScore(q, n.title), fuzzyScore(q, n.subtitle) * 0.5);
        for (const QString &k : n.keywords) s = qMax(s, fuzzyScore(q, k) * 0.7);
        if (s >= 0.2) scored << qMakePair(s, n);
    }
    std::stable_sort(scored.begin(), scored.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    QList<ActionNode> out;
    for (const auto &p : scored) out << p.second;
    return out;
}

static void findIn(const QList<Node> &l, const QString &id, ActionNode *out) {
    for (const Node &n : l) {
        if (n.n.id == id) { *out = n.n; out->childCount = int(n.kids.size()); return; }
        findIn(n.kids, id, out);
    }
}
ActionNode ActionMenu::find(const QString &id) const { ActionNode n; findIn(d->root, id, &n); return n; }

static QJsonObject nodeJson(const Node &n) {
    QJsonObject o{ { QStringLiteral("id"), n.n.id }, { QStringLiteral("title"), n.n.title }, { QStringLiteral("subtitle"), n.n.subtitle }, { QStringLiteral("icon"), n.n.icon } };
    if (n.n.isGroup) {
        QJsonArray k;
        for (const Node &c : n.kids) k.append(nodeJson(c));
        o.insert(QStringLiteral("items"), k);
    } else {
        o.insert(QStringLiteral("confirm"), n.n.action.confirm);
    }
    return o;
}
QJsonObject ActionMenu::toJson() const {
    QJsonArray a;
    for (const Node &n : d->root) a.append(nodeJson(n));
    return { { QStringLiteral("groups"), a } };
}

}  // namespace taos::search
