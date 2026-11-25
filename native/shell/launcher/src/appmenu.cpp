// SPDX-License-Identifier: AGPL-3.0-or-later
#include "appmenu.h"

#include <QCollator>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocale>
#include <QSaveFile>
#include <QSet>
#include <algorithm>

namespace taos::search {

// ------------------------------------------------------------- categories

QStringList menuCategories() {
    return { QStringLiteral("Internet"), QStringLiteral("Development"), QStringLiteral("Office"), QStringLiteral("Graphics"),
             QStringLiteral("Media"), QStringLiteral("Games"), QStringLiteral("System"), QStringLiteral("taOS"), QStringLiteral("Other") };
}

namespace {
// freedesktop category -> menu category. Utility, Core, Education, Science and unknown names have no row: they
// fall through to the next category of the entry, then to Other.
const QHash<QString, QString> &categoryTable() {
    static const QHash<QString, QString> t = [] {
        QHash<QString, QString> h;
        const auto add = [&](const QString &to, const QStringList &from) { for (const QString &f : from) h.insert(f, to); };
        add(QStringLiteral("Internet"), { "Network", "WebBrowser", "Email", "InstantMessaging", "Chat", "IRCClient", "Feed", "FileTransfer", "P2P",
                                          "RemoteAccess", "News", "VideoConference", "Telephony", "WebDevelopment_" });
        add(QStringLiteral("Development"), { "Development", "IDE", "Debugger", "RevisionControl", "WebDevelopment", "Building", "GUIDesigner",
                                             "Profiling", "Translation", "ProjectManagement" });
        add(QStringLiteral("Office"), { "Office", "WordProcessor", "Spreadsheet", "Presentation", "Calendar", "ContactManagement", "Database",
                                        "Dictionary", "Chart", "Finance", "FlowChart", "PDFViewer_", "Publishing" });
        add(QStringLiteral("Graphics"), { "Graphics", "2DGraphics", "3DGraphics", "VectorGraphics", "RasterGraphics", "Photography", "Scanning", "OCR" });
        add(QStringLiteral("Media"), { "AudioVideo", "Audio", "Video", "Player", "Recorder", "Music", "Midi", "Mixer", "Sequencer", "Tuner", "TV",
                                       "AudioVideoEditing", "DiscBurning", "Player" });
        add(QStringLiteral("Games"), { "Game" });
        add(QStringLiteral("System"), { "System", "Settings", "Monitor", "TerminalEmulator", "FileManager", "PackageManager", "Security",
                                        "HardwareSettings", "DesktopSettings", "Emulator_", "FileTools" });
        return h;
    }();
    return t;
}
}  // namespace

QString menuCategoryFor(const DesktopEntry &e) {
    if (e.id.startsWith(QLatin1String("my.taos."))) return QStringLiteral("taOS");   // the built in apps, whatever they say
    for (const QString &c : e.categories) {
        const auto it = categoryTable().constFind(c);
        if (it != categoryTable().constEnd()) return *it;
        if (c.endsWith(QLatin1String("Game")) && c != QLatin1String("Game")) return QStringLiteral("Games");   // BoardGame, ActionGame ...
    }
    return QStringLiteral("Other");
}

// ------------------------------------------------------------- helpers

namespace {
QString foldKey(const QString &s) {
    QString out;
    for (const QChar c : s.normalized(QString::NormalizationForm_D)) {
        if (c.category() == QChar::Mark_NonSpacing) continue;   // accents: Émile sorts with E
        out += c.toLower();
    }
    return out;
}

bool lessName(const MenuApp &a, const MenuApp &b) {
    static const QCollator col = [] { QCollator c(QLocale(QLocale::English)); c.setCaseSensitivity(Qt::CaseInsensitive); c.setNumericMode(true); c.setIgnorePunctuation(false); return c; }();
    const int r = col.compare(foldKey(a.name), foldKey(b.name));
    if (r != 0) return r < 0;
    return a.id < b.id;
}

QString letterOf(const MenuApp &a) {
    for (const QChar c : foldKey(a.name)) {
        if (c.isLetter()) { const QChar u = c.toUpper(); return u.unicode() < 128 ? QString(u) : QStringLiteral("#"); }
        if (c.isDigit()) return QStringLiteral("#");
    }
    return QStringLiteral("#");
}

QString configHome() {
    QString base = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.config");
    return base;
}

QJsonArray appsJson(const QList<MenuApp> &l, const AppMenu &m) {
    QJsonArray a;
    for (const MenuApp &x : l) a.append(m.appJson(x));
    return a;
}

// what the dock pins when the person has not chosen: one slot per candidate group, first installed wins
const QList<QStringList> &defaultCandidates() {
    static const QList<QStringList> c = {
        { "my.taos.files" }, { "foot", "my.taos.terminal" }, { "my.taos.browser", "chromium", "org.chromium.Chromium", "firefox" },
        { "my.taos.dashboard" }, { "my.taos.notifications" }, { "my.taos.decisions" }, { "my.taos.agents" }, { "my.taos.messages" }, { "my.taos.settings" } };
    return c;
}
}  // namespace

QString AppMenu::normId(const QString &id) { return id.endsWith(QLatin1String(".desktop")) ? id : id + QStringLiteral(".desktop"); }

AppMenu::Paths AppMenu::defaultPaths() {
    return { configHome() + QStringLiteral("/taos/startmenu.json"), configHome() + QStringLiteral("/taos/dock.json") };
}

AppMenu::AppMenu(std::shared_ptr<AppsProvider> apps, std::shared_ptr<Frecency> frec, Paths paths, bool watch, QObject *parent)
    : QObject(parent), m_apps(std::move(apps)), m_frec(std::move(frec)), m_paths(std::move(paths)), m_watching(watch) {
    if (m_watching) {
        m_debounce.setSingleShot(true);
        m_debounce.setInterval(150);
        connect(&m_debounce, &QTimer::timeout, this, [this] { refresh(); });
        connect(&m_watch, &QFileSystemWatcher::directoryChanged, &m_debounce, qOverload<>(&QTimer::start));
        connect(&m_watch, &QFileSystemWatcher::fileChanged, &m_debounce, qOverload<>(&QTimer::start));
        QDir().mkpath(QFileInfo(m_paths.pinsPath).absolutePath());
        watchDirs();
    }
}

void AppMenu::watchDirs() {
    // every applications folder and the folders below it, the .desktop files themselves (an edit in place does not
    // touch the folder), the nearest existing ancestor of a folder that does not exist yet, and the pin files' folders
    QSet<QString> want;
    const auto addTree = [&](const QString &root) {
        QString d = root;
        while (!QFileInfo(d).isDir() && d.size() > 1) d = QFileInfo(d).absolutePath();
        want.insert(d);
        if (d != root) return;
        QDirIterator it(root, QDir::Dirs | QDir::NoDotAndDotDot, QDirIterator::Subdirectories | QDirIterator::FollowSymlinks);
        int n = 0;
        while (it.hasNext() && ++n < 500) want.insert(it.next());
        QDirIterator fi(root, { QStringLiteral("*.desktop") }, QDir::Files, QDirIterator::Subdirectories | QDirIterator::FollowSymlinks);
        n = 0;
        while (fi.hasNext() && ++n < 3000) want.insert(fi.next());
    };
    for (const QString &d : m_apps->dirs()) addTree(d);
    want.insert(QFileInfo(m_paths.pinsPath).absolutePath());
    if (!m_paths.dockPath.isEmpty()) want.insert(QFileInfo(m_paths.dockPath).absolutePath());
    if (QFileInfo::exists(m_paths.dockPath)) want.insert(m_paths.dockPath);
    const QStringList have = m_watch.files() + m_watch.directories();
    QStringList drop;
    for (const QString &h : have) if (!want.contains(h)) drop << h;
    if (!drop.isEmpty()) m_watch.removePaths(drop);
    QStringList add;
    for (const QString &w : want) if (!have.contains(w) && QFileInfo::exists(w)) add << w;
    if (!add.isEmpty()) m_watch.addPaths(add);
}

void AppMenu::refresh() {
    m_apps->reload();
    m_pinsLoaded = false;
    if (m_watching) watchDirs();   // new folders, files replaced by a rename
    emit changed();
}

MenuApp AppMenu::make(const DesktopEntry &e) const {
    MenuApp a;
    a.id = e.id;
    a.name = e.name;
    a.genericName = e.genericName;
    a.comment = e.comment;
    a.icon = e.icon;
    a.category = menuCategoryFor(e);
    a.group = e.appGroup;
    a.containerApp = e.containerApp;
    a.webApp = e.webApp;
    a.terminal = e.terminal;
    a.argv = execToArgv(e.exec, e);
    if (!e.webApp.isEmpty()) a.kind = QStringLiteral("web");
    else if (!e.containerApp.isEmpty() || !e.appGroup.isEmpty()) a.kind = QStringLiteral("group");
    else if (e.id.startsWith(QLatin1String("my.taos."))) a.kind = QStringLiteral("taos");
    else a.kind = QStringLiteral("app");
    return a;
}

QList<MenuApp> AppMenu::all() const {
    const QStringList pins = pinnedIds();
    QList<MenuApp> out;
    for (const DesktopEntry &e : m_apps->entries()) {
        MenuApp a = make(e);
        a.pinned = pins.contains(a.id);
        out << a;
    }
    std::sort(out.begin(), out.end(), lessName);
    return out;
}

int AppMenu::count() const { return m_apps->entries().size(); }

QList<MenuSection> AppMenu::byCategory() const {
    const QList<MenuApp> all = this->all();
    QList<MenuSection> out;
    for (const QString &c : menuCategories()) {
        MenuSection s;
        s.title = c;
        for (const MenuApp &a : all) if (a.category == c) s.apps << a;
        if (!s.apps.isEmpty()) out << s;
    }
    return out;
}

QList<MenuSection> AppMenu::byLetter() const {
    QMap<QString, MenuSection> by;
    for (const MenuApp &a : all()) {
        const QString l = letterOf(a);
        by[l].title = l;
        by[l].apps << a;
    }
    QList<MenuSection> out;
    for (auto it = by.cbegin(); it != by.cend(); ++it) if (it.key() != QLatin1String("#")) out << it.value();
    if (by.contains(QStringLiteral("#"))) out << by.value(QStringLiteral("#"));
    return out;
}

MenuApp AppMenu::find(const QString &id) const {
    const QString n = normId(id);
    for (const DesktopEntry &e : m_apps->entries()) if (e.id == n) { MenuApp a = make(e); a.pinned = isPinned(n); return a; }
    return {};
}

// ------------------------------------------------------------------- pins

namespace {
bool readIds(const QString &path, const QString &key, QStringList *out) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return false;
    const QJsonDocument d = QJsonDocument::fromJson(f.read(1024 * 1024));
    if (!d.isObject() || !d.object().value(key).isArray()) return false;
    out->clear();
    for (const QJsonValue &v : d.object().value(key).toArray()) if (v.isString() && !v.toString().isEmpty()) *out << AppMenu::normId(v.toString());
    return true;
}
}  // namespace

QStringList AppMenu::pinnedIds() const {
    auto *self = const_cast<AppMenu *>(this);
    if (m_pinsLoaded) return m_pinsCache;
    QStringList ids;
    if (!readIds(m_paths.pinsPath, QStringLiteral("pinned"), &ids)) {
        if (!(!m_paths.dockPath.isEmpty() && readIds(m_paths.dockPath, QStringLiteral("pinned"), &ids))) {
            ids.clear();
            const QList<DesktopEntry> entries = m_apps->entries();
            for (const QStringList &slot : defaultCandidates())
                for (const QString &c : slot) {
                    const QString n = normId(c);
                    const bool have = std::any_of(entries.cbegin(), entries.cend(), [&](const DesktopEntry &e) { return e.id == n; });
                    if (have) { ids << n; break; }
                }
        }
    }
    ids.removeDuplicates();
    self->m_pinsCache = ids;
    self->m_pinsLoaded = true;
    return ids;
}

QList<MenuApp> AppMenu::pinned() const {
    QList<MenuApp> out;
    for (const QString &id : pinnedIds()) {
        MenuApp a = find(id);
        if (a.valid()) { a.pinned = true; out << a; }
    }
    return out;
}

bool AppMenu::isPinned(const QString &id) const { return pinnedIds().contains(normId(id)); }

bool AppMenu::writePins(const QStringList &ids, QString *error) {
    QJsonArray a;
    for (const QString &i : ids) a.append(i);
    QDir().mkpath(QFileInfo(m_paths.pinsPath).absolutePath());
    QSaveFile f(m_paths.pinsPath);
    if (!f.open(QIODevice::WriteOnly) || f.write(QJsonDocument(QJsonObject{ { QStringLiteral("pinned"), a } }).toJson(QJsonDocument::Indented)) < 0 || !f.commit()) {
        if (error) *error = QStringLiteral("cannot write %1").arg(m_paths.pinsPath);
        return false;
    }
    m_pinsCache = ids;
    m_pinsLoaded = true;
    emit changed();
    return true;
}

bool AppMenu::pin(const QString &id, QString *error) {
    const QString n = normId(id);
    if (!find(n).valid()) { if (error) *error = QStringLiteral("no application '%1' is installed").arg(id); return false; }
    QStringList ids = pinnedIds();
    if (ids.contains(n)) return true;
    ids << n;
    return writePins(ids, error);
}

bool AppMenu::unpin(const QString &id, QString *error) {
    const QString n = normId(id);
    QStringList ids = pinnedIds();
    if (!ids.contains(n)) { if (error) *error = QStringLiteral("'%1' is not pinned").arg(id); return false; }
    ids.removeAll(n);
    return writePins(ids, error);
}

bool AppMenu::movePinned(const QString &id, int toIndex, QString *error) {
    const QString n = normId(id);
    QStringList ids = pinnedIds();
    const int from = ids.indexOf(n);
    if (from < 0) { if (error) *error = QStringLiteral("'%1' is not pinned").arg(id); return false; }
    if (toIndex < 0 || toIndex >= ids.size()) { if (error) *error = QStringLiteral("position %1 is out of range (0 to %2)").arg(toIndex).arg(ids.size() - 1); return false; }
    ids.move(from, toIndex);
    return writePins(ids, error);
}

QList<MenuApp> AppMenu::recent(int n) const {
    QList<MenuApp> out;
    const QStringList pins = pinnedIds();
    for (const QString &fid : m_frec->top(QStringLiteral("app:"), 64)) {
        const QString id = fid.mid(4);
        if (pins.contains(id)) continue;
        MenuApp a = find(id);
        if (!a.valid()) continue;
        out << a;
        if (out.size() >= n) break;
    }
    return out;
}

// ------------------------------------------------------------ what to do

QStringList AppMenu::uninstallArgv(const MenuApp &a) const {
    if (a.kind == QLatin1String("web") && !a.webApp.isEmpty()) return { QStringLiteral("taos-webapp"), QStringLiteral("remove"), a.webApp };
    if (a.kind == QLatin1String("group") && !a.containerApp.isEmpty()) return { QStringLiteral("taos-appgroups"), QStringLiteral("remove"), a.containerApp };
    return {};
}

QList<Action> AppMenu::contextActions(const MenuApp &a) const {
    QList<Action> out;
    Action open{ QStringLiteral("open"), QStringLiteral("Open"), QStringLiteral("launch"), a.argv, a.terminal ? QStringLiteral("terminal") : QString() };
    out << open;
    if (a.pinned || isPinned(a.id)) out << Action{ QStringLiteral("unpin"), QStringLiteral("Unpin from menu"), QStringLiteral("menu-unpin"), {}, a.id };
    else out << Action{ QStringLiteral("pin"), QStringLiteral("Pin to menu"), QStringLiteral("menu-pin"), {}, a.id };
    if (executableExists(QStringLiteral("taos-dock"))) {
        QString d = a.id;
        d.chop(8);
        out << Action{ QStringLiteral("dock-pin"), QStringLiteral("Keep in Dock"), QStringLiteral("exec"), { QStringLiteral("taos-dock"), QStringLiteral("pin"), d }, {} };
    }
    out << Action{ QStringLiteral("info"), QStringLiteral("App info"), QStringLiteral("menu-info"), {}, a.id };
    if (a.kind == QLatin1String("group")) out << Action{ QStringLiteral("move-group"), QStringLiteral("Move to group"), QStringLiteral("menu-move"), {}, a.id };
    const QStringList un = uninstallArgv(a);
    if (!un.isEmpty()) {
        Action u{ QStringLiteral("uninstall"), QStringLiteral("Uninstall"), QStringLiteral("exec"), un, {} };
        u.confirm = true;
        out << u;
    }
    return out;
}

QJsonObject AppMenu::appJson(const MenuApp &a) const {
    return { { QStringLiteral("id"), a.id }, { QStringLiteral("name"), a.name }, { QStringLiteral("genericName"), a.genericName },
             { QStringLiteral("comment"), a.comment }, { QStringLiteral("icon"), a.icon }, { QStringLiteral("category"), a.category },
             { QStringLiteral("group"), a.group }, { QStringLiteral("kind"), a.kind }, { QStringLiteral("pinned"), a.pinned },
             { QStringLiteral("terminal"), a.terminal }, { QStringLiteral("argv"), QJsonArray::fromStringList(a.argv) } };
}

QJsonObject AppMenu::toJson() const {
    QJsonArray cats;
    for (const MenuSection &s : byCategory()) cats.append(QJsonObject{ { QStringLiteral("title"), s.title }, { QStringLiteral("apps"), appsJson(s.apps, *this) } });
    QJsonArray letters;
    for (const MenuSection &s : byLetter()) letters.append(QJsonObject{ { QStringLiteral("title"), s.title }, { QStringLiteral("apps"), appsJson(s.apps, *this) } });
    return { { QStringLiteral("count"), count() }, { QStringLiteral("pinned"), appsJson(pinned(), *this) },
             { QStringLiteral("recent"), appsJson(recent(), *this) }, { QStringLiteral("categories"), cats }, { QStringLiteral("letters"), letters } };
}

}  // namespace taos::search
