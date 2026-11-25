// SPDX-License-Identifier: AGPL-3.0-or-later
#include "menucontroller.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QStandardPaths>
#include <pwd.h>
#include <unistd.h>

#include "iconprovider.h"
#include "rowmodel.h"
#include "runner.h"

using namespace taos::search;

namespace taos::launcher {

// ---------------------------------------------------------------- models

QHash<int, QByteArray> AppListModel::roleNames() const {
    return { { IdRole, "appId" }, { NameRole, "name" }, { IconRole, "icon" }, { HasIconRole, "hasIcon" }, { GroupRole, "group" }, { CategoryRole, "category" },
             { SectionRole, "section" }, { FirstInSectionRole, "firstInSection" }, { PinnedRole, "pinned" }, { KindRole, "kind" }, { SubtitleRole, "subtitle" },
             { AccessibleRole, "accessibleText" } };
}

QVariant AppListModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() >= m_apps.size()) return {};
    const MenuApp &a = m_apps[i.row()];
    const QString sec = i.row() < m_sections.size() ? m_sections[i.row()] : QString();
    switch (role) {
    case IdRole: return a.id;
    case NameRole: return a.name;
    case IconRole: return RowModel::iconUrl(a.icon);
    case HasIconRole: return !RowModel::iconUrl(a.icon).isEmpty();
    case GroupRole: return a.group;
    case CategoryRole: return a.category;
    case SectionRole: return sec;
    case FirstInSectionRole: return !sec.isEmpty() && (i.row() == 0 || m_sections.value(i.row() - 1) != sec);
    case PinnedRole: return a.pinned;
    case KindRole: return a.kind;
    case SubtitleRole: return !a.group.isEmpty() ? a.group : a.genericName;
    case AccessibleRole: return a.group.isEmpty() ? a.name : QStringLiteral("%1, in %2").arg(a.name, a.group);
    }
    return {};
}

void AppListModel::setApps(const QList<MenuApp> &apps, const QStringList &sections) {
    beginResetModel();
    m_apps = apps;
    m_sections = sections;
    endResetModel();
}

QHash<int, QByteArray> ActionListModel::roleNames() const {
    return { { IdRole, "actionId" }, { TitleRole, "title" }, { SubtitleRole, "subtitle" }, { IconRole, "icon" }, { HasIconRole, "hasIcon" },
             { IsGroupRole, "isGroup" }, { ConfirmRole, "confirm" }, { AccessibleRole, "accessibleText" } };
}

QVariant ActionListModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() >= m_nodes.size()) return {};
    const ActionNode &n = m_nodes[i.row()];
    switch (role) {
    case IdRole: return n.id;
    case TitleRole: return n.title;
    case SubtitleRole: return n.subtitle;
    case IconRole: return RowModel::iconUrl(n.icon);
    case HasIconRole: return !RowModel::iconUrl(n.icon).isEmpty();
    case IsGroupRole: return n.isGroup;
    case ConfirmRole: return n.action.confirm;
    case AccessibleRole: return n.isGroup ? QStringLiteral("%1, %2 actions").arg(n.title).arg(n.childCount) : n.title;
    }
    return {};
}

void ActionListModel::setNodes(const QList<ActionNode> &n) {
    beginResetModel();
    m_nodes = n;
    endResetModel();
}

// ------------------------------------------------------------ controller

MenuController::MenuController(std::shared_ptr<AppMenu> apps, std::shared_ptr<ActionMenu> actions, QObject *parent)
    : QObject(parent), m_apps(std::move(apps)), m_actionsEngine(std::move(actions)) {
    if (const passwd *pw = getpwuid(getuid())) {
        m_user = QString::fromLocal8Bit(pw->pw_gecos).section(QLatin1Char(','), 0, 0).trimmed();
        if (m_user.isEmpty()) m_user = QString::fromLocal8Bit(pw->pw_name);
        for (const QString &p : { QDir::homePath() + QStringLiteral("/.face"), QStringLiteral("/var/lib/AccountsService/icons/") + QString::fromLocal8Bit(pw->pw_name) })
            if (QFileInfo(p).isFile()) { m_avatar = QStringLiteral("file://") + p; break; }
    }
    if (m_user.isEmpty()) m_user = qEnvironmentVariable("USER");
    connect(m_apps.get(), &AppMenu::changed, this, [this] { clearIconCache(); reload(); });
    m_nav.setOrder({ QStringLiteral("pinned"), QStringLiteral("recent"), QStringLiteral("all"), QStringLiteral("footer") });
    reload();
}

QString MenuController::actionTitle() const {
    if (m_path.isEmpty()) return QStringLiteral("Actions");
    return m_actionsEngine->find(m_path.last()).title.isEmpty() ? QStringLiteral("Actions") : m_actionsEngine->find(m_path.last()).title;
}

QStringList MenuController::footerIds() const { QStringList l; for (const Footer &f : m_footer) l << f.id; return l; }
QStringList MenuController::footerLabels() const { QStringList l; for (const Footer &f : m_footer) l << f.label; return l; }

QStringList MenuController::contextLabels() const { QStringList l; for (const Action &a : m_ctx) l << a.label; return l; }
QStringList MenuController::contextDanger() const { QStringList l; for (const Action &a : m_ctx) l << (a.confirm ? QStringLiteral("1") : QString()); return l; }

static QString filesDesktopId(const AppMenu &m) {
    for (const MenuApp &a : m.all()) if (a.id == QLatin1String("my.taos.files.desktop")) return a.id;
    for (const MenuApp &a : m.all()) if (a.name == QLatin1String("Files")) return a.id;
    return {};
}

void MenuController::reload() {
    const QList<MenuApp> pinned = m_apps->pinned();
    m_pinned.setApps(pinned);
    m_recent.setApps(m_apps->recent(6));
    QList<MenuApp> all;
    QStringList sec;
    if (m_style == QLatin1String("anchored") && m_sort == QLatin1String("category")) {
        for (const MenuApp &a : m_apps->recent(3)) { all << a; sec << QStringLiteral("Recent"); }   // the left list leads with what you use
    }
    for (const MenuSection &s : m_sort == QLatin1String("az") ? m_apps->byLetter() : m_apps->byCategory())
        for (const MenuApp &a : s.apps) { all << a; sec << s.title; }
    m_all.setApps(all, sec);
    m_footer.clear();
    if (m_apps->find(QStringLiteral("my.taos.settings")).valid() || executableExists(QStringLiteral("taos-settings"))) m_footer << Footer{ QStringLiteral("settings"), QStringLiteral("Settings") };
    if (!filesDesktopId(*m_apps).isEmpty() || executableExists(QStringLiteral("xdg-open"))) m_footer << Footer{ QStringLiteral("files"), QStringLiteral("Files") };
    if (!m_actionsEngine->level({}).isEmpty()) m_footer << Footer{ QStringLiteral("actions"), QStringLiteral("Actions") };
    m_powerModel.setNodes(m_actionsEngine->level({ QStringLiteral("system") }));
    if (m_footer.isEmpty() || m_powerModel.count() > 0) m_footer << Footer{ QStringLiteral("power"), QStringLiteral("Power") };
    refreshActions();
    syncNav();
    emit footerChanged();
    emit modelsChanged();
}

void MenuController::refreshActions() {
    m_actionModel.setNodes(m_actionsEngine->filter(m_path, m_filter));
    if (m_actionIdx >= m_actionModel.count()) m_actionIdx = qMax(0, m_actionModel.count() - 1);
}

void MenuController::syncNav() {
    m_nav.setZone(QStringLiteral("pinned"), m_pinned.count(), m_nav.columns(QStringLiteral("pinned")));
    m_nav.setZone(QStringLiteral("recent"), m_style == QLatin1String("centered") ? m_recent.count() : 0, m_nav.columns(QStringLiteral("recent")));
    m_nav.setZone(QStringLiteral("all"), m_all.count(), m_nav.columns(QStringLiteral("all")));
    m_nav.setZone(QStringLiteral("footer"), m_footer.size(), qMax(1, m_footer.size()));
}

void MenuController::setLayout(const QString &style) {
    m_style = style;
    if (style == QLatin1String("anchored")) { m_nav.setOrder({ QStringLiteral("all"), QStringLiteral("footer") }); m_nav.setLink(QStringLiteral("all"), QStringLiteral("pinned")); }
    else if (style == QLatin1String("ring") || style == QLatin1String("text")) { m_nav.setOrder({ QStringLiteral("pinned"), QStringLiteral("footer") }); m_nav.setLink({}, {}); }
    else if (style == QLatin1String("phone")) { m_nav.setOrder({ QStringLiteral("pinned"), QStringLiteral("all") }); m_nav.setLink({}, {}); }
    else { m_nav.setOrder({ QStringLiteral("pinned"), QStringLiteral("recent"), QStringLiteral("all"), QStringLiteral("footer") }); m_nav.setLink({}, {}); }
    reload();
}

void MenuController::setColumns(const QString &zone, int columns) {
    m_nav.setZone(zone, m_nav.count(zone), columns);
}

void MenuController::setSort(const QString &s) {
    const QString v = s == QLatin1String("az") ? s : QStringLiteral("category");
    if (v == m_sort) return;
    m_sort = v;
    reload();
    emit sortChanged();
}
void MenuController::toggleSort() { setSort(m_sort == QLatin1String("az") ? QStringLiteral("category") : QStringLiteral("az")); }

void MenuController::reset() {
    m_nav.clear();
    m_actionsMode = false;
    m_path.clear();
    m_filter.clear();
    m_actionIdx = 0;
    m_ctx.clear();
    m_infoOpen = false;
    m_powerOpen = false;
    m_confirmId.clear();
    reload();
    emit actionsModeChanged();
    emit cursorChanged();
    emit contextChanged();
}

MenuApp MenuController::currentApp() const {
    const QString z = m_nav.zone();
    const AppListModel *m = z == QLatin1String("pinned") ? &m_pinned : z == QLatin1String("recent") ? &m_recent : z == QLatin1String("all") ? &m_all : nullptr;
    if (!m) return {};
    const MenuApp *a = m->at(m_nav.index());
    return a ? *a : MenuApp();
}

// ---------------------------------------------------------------- keys

bool MenuController::move(int dx, int dy) {
    if (m_infoOpen) return false;
    if (!m_ctx.isEmpty()) {
        if (dy != 0) { m_ctxIdx = (m_ctxIdx + dy + m_ctx.size()) % m_ctx.size(); m_confirmId.clear(); emit contextChanged(); return true; }
        return true;
    }
    if (m_powerOpen) {
        const int n = m_powerModel.count();
        if (n && dy != 0) { m_powerIdx = (m_powerIdx + dy + n) % n; m_confirmId.clear(); emit cursorChanged(); emit contextChanged(); }
        return true;
    }
    if (m_actionsMode) {
        const int n = m_actionModel.count();
        if (n && dy != 0) { m_actionIdx = (m_actionIdx + dy + n) % n; m_confirmId.clear(); emit cursorChanged(); emit contextChanged(); }
        return true;
    }
    const bool moved = m_nav.move(dx, dy);
    if (moved) { m_confirmId.clear(); emit cursorChanged(); emit contextChanged(); }
    return moved;
}

void MenuController::setActionIndex(int i) { if (i >= 0 && i < m_actionModel.count() && i != m_actionIdx) { m_actionIdx = i; m_confirmId.clear(); emit cursorChanged(); emit contextChanged(); } }

void MenuController::hover(const QString &zone, int index) {
    if (!m_ctx.isEmpty() || m_infoOpen || m_powerOpen) return;
    if (m_nav.select(zone, index)) { emit cursorChanged(); }
}

Result MenuController::resultFor(const MenuApp &a) const {
    Result r;
    r.id = QStringLiteral("app:") + a.id;
    r.provider = QStringLiteral("apps");
    r.kind = QStringLiteral("app");
    r.title = a.name;
    r.icon = a.icon;
    r.actions = m_apps->contextActions(a);
    return r;
}

Result MenuController::resultFor(const ActionNode &n) const {
    Result r;
    r.id = QStringLiteral("action:") + n.id;
    r.provider = QStringLiteral("actions");
    r.kind = QStringLiteral("command");
    r.title = n.title;
    r.icon = n.icon;
    r.actions << n.action;
    return r;
}

bool MenuController::performApp(const MenuApp &a) {
    if (!a.valid() || !m_perform) return false;
    return m_perform(resultFor(a), 0);
}

bool MenuController::performNode(const ActionNode &n) {
    if (!m_perform) return false;
    Result r = resultFor(n);
    if (n.action.confirm) {
        if (m_confirmId != n.id) {
            m_confirmId = n.id;
            emit contextChanged();
            say(QStringLiteral("Press Enter again to confirm: %1").arg(n.title));
            return false;
        }
        m_confirmId.clear();
        r.actions[0].confirm = false;   // the second Enter is the confirmation
        emit contextChanged();
    }
    return m_perform(r, 0);
}

static QString footerAction(const QString &id) { return id; }

void MenuController::runFooter(int i) {
    if (i < 0 || i >= m_footer.size()) return;
    const QString id = footerAction(m_footer[i].id);
    if (id == QLatin1String("settings")) {
        const MenuApp s = m_apps->find(QStringLiteral("my.taos.settings"));
        if (s.valid()) performApp(s);
        else if (m_perform) { Result r; r.id = QStringLiteral("cmd:settings"); r.kind = QStringLiteral("command"); r.title = QStringLiteral("Settings"); Action a; a.id = QStringLiteral("open"); a.verb = QStringLiteral("exec"); a.args = { QStringLiteral("taos-settings") }; r.actions << a; m_perform(r, 0); }
    } else if (id == QLatin1String("files")) {
        const QString fid = filesDesktopId(*m_apps);
        if (!fid.isEmpty()) performApp(m_apps->find(fid));
        else if (m_perform) { Result r; r.id = QStringLiteral("cmd:files"); r.kind = QStringLiteral("command"); r.title = QStringLiteral("Files"); Action a; a.id = QStringLiteral("open"); a.verb = QStringLiteral("open"); a.data = QDir::homePath(); r.actions << a; m_perform(r, 0); }
    } else if (id == QLatin1String("actions")) {
        enterActions(true);
    } else if (id == QLatin1String("power")) {
        openPower();
    }
}

bool MenuController::enter() {
    if (m_infoOpen) { m_infoOpen = false; emit contextChanged(); return true; }
    if (!m_ctx.isEmpty()) { runContext(m_ctxIdx); return true; }
    if (m_powerOpen) { runPower(m_powerIdx); return true; }
    if (m_actionsMode) {
        const ActionNode *n = m_actionModel.at(m_actionIdx);
        if (!n) return true;
        if (n->isGroup) { m_path << n->id; m_filter.clear(); m_actionIdx = 0; refreshActions(); emit actionsModeChanged(); emit cursorChanged(); }
        else performNode(*n);
        return true;
    }
    if (!m_nav.active()) return false;
    activate(m_nav.zone(), m_nav.index());
    return true;
}

void MenuController::activate(const QString &zone, int index) {
    if (zone == QLatin1String("footer")) { runFooter(index); return; }
    const AppListModel *m = zone == QLatin1String("pinned") ? &m_pinned : zone == QLatin1String("recent") ? &m_recent : zone == QLatin1String("all") ? &m_all : nullptr;
    if (!m) return;
    if (const MenuApp *a = m->at(index)) performApp(*a);
}

bool MenuController::back() {
    if (m_infoOpen) { m_infoOpen = false; emit contextChanged(); return true; }
    if (!m_confirmId.isEmpty()) { m_confirmId.clear(); emit contextChanged(); return true; }   // Escape cancels a pending confirmation first
    if (!m_ctx.isEmpty()) { closeContext(); return true; }
    if (m_powerOpen) { closePower(); return true; }
    if (m_actionsMode && !m_path.isEmpty()) return actionsUp();
    return false;
}

// ------------------------------------------------------------- context

void MenuController::openContextFor(const QString &zone, int index) {
    const AppListModel *m = zone == QLatin1String("pinned") ? &m_pinned : zone == QLatin1String("recent") ? &m_recent : zone == QLatin1String("all") ? &m_all : nullptr;
    const MenuApp *a = m ? m->at(index) : nullptr;
    if (!a) return;
    m_nav.select(zone, index);
    m_ctxApp = *a;
    m_ctx = m_apps->contextActions(*a);
    m_ctxIdx = 0;
    m_confirmId.clear();
    emit cursorChanged();
    emit contextChanged();
}

void MenuController::openContext() {
    if (m_nav.active()) openContextFor(m_nav.zone(), m_nav.index());
}

void MenuController::closeContext() {
    if (m_ctx.isEmpty()) return;
    m_ctx.clear();
    m_confirmId.clear();
    emit contextChanged();
}

void MenuController::setContextIndex(int i) { if (i >= 0 && i < m_ctx.size() && i != m_ctxIdx) { m_ctxIdx = i; m_confirmId.clear(); emit contextChanged(); } }

void MenuController::runContext(int i) {
    if (i < 0 || i >= m_ctx.size() || !m_perform) return;
    Action a = m_ctx[i];
    const QString key = QStringLiteral("ctx:") + a.id;
    if (a.confirm) {
        if (m_confirmId != key) { m_confirmId = key; emit contextChanged(); say(QStringLiteral("Press Enter again to %1 %2").arg(a.label.toLower(), m_ctxApp.name)); return; }
        m_confirmId.clear();
        a.confirm = false;
    }
    const MenuApp app = m_ctxApp;
    if (a.verb.startsWith(QLatin1String("menu-"))) { m_ctx.clear(); emit contextChanged(); handleVerb(a); return; }
    m_ctx.clear();
    emit contextChanged();
    Result r = resultFor(app);
    r.actions = { a };
    m_perform(r, 0);
}

bool MenuController::handleVerb(const Action &a) {
    QString err;
    const MenuApp app = m_apps->find(a.data);
    if (a.verb == QLatin1String("menu-pin")) {
        if (!m_apps->pin(a.data, &err)) { say(err); return false; }
        say(QStringLiteral("Pinned %1").arg(app.name));
        return true;
    }
    if (a.verb == QLatin1String("menu-unpin")) {
        if (!m_apps->unpin(a.data, &err)) { say(err); return false; }
        say(QStringLiteral("Unpinned %1").arg(app.name));
        return true;
    }
    if (a.verb == QLatin1String("menu-info")) {
        m_ctxApp = app;
        m_info = infoFor(a.data);
        m_infoOpen = true;
        emit contextChanged();
        return true;
    }
    if (a.verb == QLatin1String("menu-move")) {
        // the groups come from the app groups engine; the move itself is `taos-appgroups move <app> --to group:<name>`
        QProcess p;
        p.start(QStringLiteral("taos-appgroups"), { QStringLiteral("groups"), QStringLiteral("--json") });
        QStringList groups;
        if (p.waitForFinished(1500) && p.exitCode() == 0) {
            const QJsonDocument d = QJsonDocument::fromJson(p.readAllStandardOutput());
            const QJsonArray arr = d.isArray() ? d.array() : d.object().value(QStringLiteral("groups")).toArray();
            for (const QJsonValue &v : arr) {
                const QString n = v.isString() ? v.toString() : v.toObject().value(QStringLiteral("name")).toString();
                if (!n.isEmpty() && n != app.group) groups << n;
            }
        } else { if (p.state() != QProcess::NotRunning) p.kill(); say(QStringLiteral("Could not list the app groups")); return false; }
        if (groups.isEmpty()) { say(QStringLiteral("There is no other app group to move to")); return false; }
        m_ctxApp = app;
        m_ctx.clear();
        for (const QString &g : groups) {
            Action m{ QStringLiteral("move:") + g, g, QStringLiteral("exec"), { QStringLiteral("taos-appgroups"), QStringLiteral("move"), app.containerApp, QStringLiteral("--to"), QStringLiteral("group:") + g }, {} };
            m_ctx << m;
        }
        m_ctxIdx = 0;
        emit contextChanged();
        return true;
    }
    return false;
}

QVariantMap MenuController::infoFor(const QString &appId) const {
    const MenuApp a = m_apps->find(appId);
    QVariantMap m;
    m[QStringLiteral("name")] = a.name;
    m[QStringLiteral("id")] = a.id;
    m[QStringLiteral("description")] = a.comment.isEmpty() ? a.genericName : a.comment;
    m[QStringLiteral("category")] = a.category;
    m[QStringLiteral("group")] = a.group;
    m[QStringLiteral("kind")] = a.kind == QLatin1String("group") ? QStringLiteral("App group app") : a.kind == QLatin1String("web") ? QStringLiteral("Web app")
                              : a.kind == QLatin1String("taos") ? QStringLiteral("taOS app") : QStringLiteral("Application");
    m[QStringLiteral("command")] = a.argv.join(QLatin1Char(' '));
    m[QStringLiteral("icon")] = RowModel::iconUrl(a.icon);
    m[QStringLiteral("pinned")] = a.pinned || m_apps->isPinned(a.id);
    return m;
}

void MenuController::pinToggle(const QString &appId) {
    QString err;
    if (m_apps->isPinned(appId)) m_apps->unpin(appId, &err); else m_apps->pin(appId, &err);
    if (!err.isEmpty()) say(err);
}

void MenuController::movePinned(const QString &appId, int toIndex) {
    QString err;
    if (!m_apps->movePinned(appId, toIndex, &err)) say(err);
}

// --------------------------------------------------------------- power

void MenuController::openPower() { m_powerOpen = true; m_powerIdx = 0; m_confirmId.clear(); emit contextChanged(); emit cursorChanged(); }
void MenuController::closePower() { if (!m_powerOpen) return; m_powerOpen = false; m_confirmId.clear(); emit contextChanged(); }
void MenuController::runPower(int i) {
    const ActionNode *n = m_powerModel.at(i);
    if (!n) return;
    m_powerIdx = i;
    performNode(*n);
}

// ------------------------------------------------------------- actions

void MenuController::enterActions(bool on) {
    if (on == m_actionsMode) return;
    m_actionsMode = on;
    m_path.clear();
    m_filter.clear();
    m_actionIdx = 0;
    m_confirmId.clear();
    refreshActions();
    emit actionsModeChanged();
    emit cursorChanged();
}

void MenuController::setActionFilter(const QString &text) {
    if (text == m_filter) return;
    m_filter = text;
    m_actionIdx = 0;
    refreshActions();
    emit cursorChanged();
}

bool MenuController::actionsUp() {
    if (m_path.isEmpty()) return false;
    m_path.removeLast();
    m_filter.clear();
    m_actionIdx = 0;
    refreshActions();
    emit actionsModeChanged();
    emit cursorChanged();
    return true;
}

// ------------------------------------------------------------ IPC / CLI

QString MenuController::handleCommand(const QStringList &w) {
    const QString c = w.value(0);
    const auto compact = [](const QJsonValue &v) {
        return v.isArray() ? QString::fromUtf8(QJsonDocument(v.toArray()).toJson(QJsonDocument::Compact)) : QString::fromUtf8(QJsonDocument(v.toObject()).toJson(QJsonDocument::Compact));
    };
    if (c == QLatin1String("menu")) {
        const QJsonObject o = m_apps->toJson();
        const QString part = w.value(1);
        if (part.isEmpty()) return QStringLiteral("ok ") + compact(o);
        if (o.contains(part)) return QStringLiteral("ok ") + compact(o.value(part));
        return QStringLiteral("error: menu parts are pinned, recent, categories, letters");
    }
    if (c == QLatin1String("pin") || c == QLatin1String("unpin")) {
        if (w.size() != 2) return QStringLiteral("error: usage: %1 <desktop-id>").arg(c);
        QString err;
        const bool ok = c == QLatin1String("pin") ? m_apps->pin(w[1], &err) : m_apps->unpin(w[1], &err);
        return ok ? QStringLiteral("ok") : QStringLiteral("error: ") + err;
    }
    if (c == QLatin1String("move-pin")) {
        bool ok = false;
        const int to = w.value(2).toInt(&ok);
        QString err;
        if (w.size() != 3 || !ok) return QStringLiteral("error: usage: move-pin <desktop-id> <position>");
        return m_apps->movePinned(w[1], to, &err) ? QStringLiteral("ok") : QStringLiteral("error: ") + err;
    }
    if (c == QLatin1String("actions")) {
        QStringList path = w.mid(1);
        const QList<ActionNode> l = m_actionsEngine->level(path);
        if (path.isEmpty() || !l.isEmpty()) {
            QJsonArray a;
            for (const ActionNode &n : l) a.append(QJsonObject{ { QStringLiteral("id"), n.id }, { QStringLiteral("title"), n.title }, { QStringLiteral("subtitle"), n.subtitle },
                                                              { QStringLiteral("group"), n.isGroup }, { QStringLiteral("confirm"), n.action.confirm } });
            return QStringLiteral("ok ") + compact(a);
        }
        return QStringLiteral("error: no such action group");
    }
    if (c == QLatin1String("run")) {
        const ActionNode n = m_actionsEngine->find(w.value(1));
        if (n.id.isEmpty() || n.isGroup) return QStringLiteral("error: no such action: %1").arg(w.value(1));
        if (n.action.confirm && w.value(2) != QLatin1String("confirm")) return QStringLiteral("error: %1 needs confirmation: run %2 confirm").arg(n.title, n.id);
        QString err;
        Action a = n.action;
        a.confirm = false;
        return runAction(a, &err) ? QStringLiteral("ok") : QStringLiteral("error: ") + (err.isEmpty() ? QStringLiteral("that did not work") : err);
    }
    return {};
}

}  // namespace taos::launcher
