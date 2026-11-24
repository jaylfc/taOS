#include "thememanager.h"

#include <QDir>
#include <QFileInfo>
#include <unistd.h>

ThemeManager::ThemeManager(Theme *theme, const Options &o, QObject *parent) : QObject(parent), m_theme(theme), m_o(o) {
    if (m_o.appearancePath.isEmpty()) m_o.appearancePath = AppearanceFile::userPath();
    if (m_o.systemPath.isEmpty()) m_o.systemPath = AppearanceFile::systemPath();
    if (m_o.mirrorDir.isEmpty()) m_o.mirrorDir = AppearanceFile::mirrorDir();
    if (m_o.themeDirs.isEmpty()) m_o.themeDirs = ThemeCatalog::standardDirs(m_o.role == Role::Session);
    if (m_o.builtinDir.isEmpty()) m_o.builtinDir = ThemeCatalog::builtinDir();
    if (m_o.uid < 0) m_o.uid = int(getuid());
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(25);
    connect(&m_debounce, &QTimer::timeout, this, [this] { reload(); });
    connect(&m_watch, &QFileSystemWatcher::fileChanged, this, [this] { m_debounce.start(); });
    connect(&m_watch, &QFileSystemWatcher::directoryChanged, this, [this] { m_debounce.start(); });
    // "auto" follows the clock; looking once a minute is cheap and survives suspend and clock changes.
    m_tick.setInterval(60 * 1000);
    connect(&m_tick, &QTimer::timeout, this, [this] {
        if (m_app.mode == QLatin1String("auto")) showCurrent(false);
    });
}

bool ThemeManager::start(QString *error) {
    m_theme->setTransitionMs(0);  // the first paint is not a fade
    m_cat.scan(m_o.themeDirs, m_o.builtinDir);
    m_app = readSource();
    const bool ok = [&] {
        const ThemeDef *d = m_cat.resolve(m_app.theme);
        if (!d) {
            if (error) *error = QStringLiteral("no usable theme is installed");
            return false;
        }
        return true;
    }();
    if (!ok) return false;
    showCurrent(false);
    m_theme->setTransitionMs(m_o.transitionMs);
    m_started = true;
    if (m_o.watch) rewatch();
    m_tick.start();
    if (m_o.role == Role::Session) writeMirror();
    emit themesChanged();
    return true;
}

Appearance ThemeManager::readSource() const {
    Appearance a;
    if (m_o.role == Role::Session) {
        if (QFileInfo::exists(m_o.appearancePath)) AppearanceFile::read(m_o.appearancePath, &a);
        else AppearanceFile::read(m_o.systemPath, &a);  // a new person starts from the system default
    } else {
        AppearanceFile::read(m_o.systemPath, &a);
        if (m_previewUid >= 0) {
            Appearance u;
            if (AppearanceFile::read(m_o.mirrorDir + QLatin1Char('/') + QString::number(m_previewUid) + QStringLiteral(".json"), &u)) a = u;
        }
    }
    // Command line and environment win over the file, field by field.
    if (!m_o.pinnedTheme.isEmpty() && validThemeId(m_o.pinnedTheme)) a.theme = m_o.pinnedTheme;
    if (AppearanceFile::validMode(m_o.pinnedVariant)) a.mode = m_o.pinnedVariant;
    return a;
}

void ThemeManager::showCurrent(bool announceIt) {
    const ThemeDef *d = m_cat.resolve(m_app.theme);
    if (!d) return;
    const QString oldVariant = m_variant, oldId = m_shownId;
    const QString v = AppearanceFile::resolveVariant(m_app.mode, now());
    m_theme->apply(*d, v, m_o.reduceMotion);
    m_theme->setWindowButtonsOverride(m_app.windowButtons);
    m_shownId = m_theme->id();
    m_variant = m_theme->variant();
    if (m_variant != oldVariant || m_shownId != oldId || announceIt) emitAppearance();
    if (announceIt) emit announce(announcementText());
}

void ThemeManager::reload() {
    if (!m_started) return;
    const QStringList before = [&] { QStringList l; for (const ThemeDef &d : m_cat.themes()) l << d.id + d.source; return l; }();
    m_cat.scan(m_o.themeDirs, m_o.builtinDir);
    QStringList after;
    for (const ThemeDef &d : m_cat.themes()) after << d.id + d.source;
    if (before != after) emit themesChanged();
    const Appearance a = readSource();
    const bool modeOrThemeChanged = a != m_app;
    m_app = a;
    showCurrent(false);
    if (modeOrThemeChanged) emitAppearance();
    if (modeOrThemeChanged && m_o.role == Role::Session) writeMirror();
    if (m_o.watch) rewatch();
}

void ThemeManager::rescanThemes() {
    m_cat.scan(m_o.themeDirs, m_o.builtinDir);
    emit themesChanged();
    showCurrent(false);
}

void ThemeManager::rewatch() {
    // Watch the file, its folder (the file is replaced by a rename, and may not exist yet), and
    // the theme folders. When the folder does not exist yet, the nearest folder that does.
    QStringList want;
    auto addExisting = [&](QString p) {
        QFileInfo fi(p);
        while (!fi.exists() && fi.absoluteDir().absolutePath() != fi.absoluteFilePath()) fi = QFileInfo(fi.absolutePath());
        if (fi.exists()) want << fi.absoluteFilePath();
    };
    const QString file = m_o.role == Role::Session ? m_o.appearancePath : m_o.systemPath;
    if (QFileInfo::exists(file)) want << QFileInfo(file).absoluteFilePath();
    addExisting(QFileInfo(file).absolutePath());
    if (m_o.role == Role::Session)
        for (const QString &d : std::as_const(m_o.themeDirs)) addExisting(d);
    const QStringList have = m_watch.files() + m_watch.directories();
    QStringList add;
    for (const QString &w : std::as_const(want)) if (!have.contains(w)) add << w;
    if (!add.isEmpty()) m_watch.addPaths(add);
}

QString ThemeManager::mirrorPath() const { return m_o.mirrorDir + QLatin1Char('/') + QString::number(m_o.uid) + QStringLiteral(".json"); }

void ThemeManager::writeMirror() {
    // Best effort: only when the folder exists and is writable by this person.
    const QFileInfo dir(m_o.mirrorDir);
    if (!dir.isDir() || !dir.isWritable()) return;
    AppearanceFile::write(mirrorPath(), m_app, nullptr, true);
}

void ThemeManager::previewUser(int uid) {
    if (m_o.role != Role::Greeter) return;
    m_previewUid = uid;
    reload();
}

void ThemeManager::emitAppearance() {
    emit appearanceChanged();
    emit themesChanged();  // the chips' current mark and swatches follow the variant
}

QString ThemeManager::themeName() const {
    const ThemeDef *d = m_cat.find(m_shownId);
    return d ? d->name : m_shownId;
}

QString ThemeManager::description() const {
    const ThemeDef *d = m_cat.find(m_shownId);
    return d ? d->description : QString();
}

QVariantList ThemeManager::themes() const {
    QVariantList out;
    for (const ThemeDef &d : m_cat.themes()) {
        QVariantMap m;
        m.insert(QStringLiteral("id"), d.id);
        m.insert(QStringLiteral("name"), d.name);
        m.insert(QStringLiteral("description"), d.description);
        QVariantList sw;
        const QString v = d.hasVariant(m_variant) ? m_variant : d.variants.value(0);
        for (const QColor &c : d.swatches.value(v)) sw << c;
        m.insert(QStringLiteral("swatches"), sw);
        m.insert(QStringLiteral("current"), d.id == m_shownId);
        out << m;
    }
    return out;
}

QVariantList ThemeManager::modes() const {
    static const struct { const char *id; const char *label; } kModes[] = {{"dark", "Dark"}, {"light", "Light"}, {"auto", "Auto"}};
    QVariantList out;
    for (const auto &m : kModes) {
        QVariantMap v;
        v.insert(QStringLiteral("id"), QString::fromLatin1(m.id));
        v.insert(QStringLiteral("label"), tr(m.label));
        v.insert(QStringLiteral("current"), m_app.mode == QLatin1String(m.id));
        out << v;
    }
    return out;
}

QString ThemeManager::announcementText() const {
    if (m_app.mode == QLatin1String("auto"))
        return tr("Theme: %1, auto, %2 now").arg(themeName(), m_variant == QLatin1String("light") ? tr("light") : tr("dark"));
    return tr("Theme: %1, %2").arg(themeName(), m_variant == QLatin1String("light") ? tr("light") : tr("dark"));
}

bool ThemeManager::choose(const QString &id, const QString &mode) {
    if (!m_cat.find(id) || !AppearanceFile::validMode(mode)) return false;
    Appearance a;
    a.theme = id;
    a.mode = mode;
    a.windowButtons = m_app.windowButtons;   // a theme or mode choice keeps the person's button side
    m_writeError.clear();
    m_app = a;
    showCurrent(true);  // live first, then the file: the screen must not wait for the disk
    if (m_o.role == Role::Session) {
        if (!AppearanceFile::write(m_o.appearancePath, a, &m_writeError)) {
            emit announce(tr("Could not save the theme: %1").arg(m_writeError));
            return false;
        }
        writeMirror();
        if (m_o.watch) rewatch();
    }
    emitAppearance();
    return true;
}

bool ThemeManager::setWindowButtons(const QString &choice) {
    if (choice != QLatin1String("theme") && !validButtonSide(choice)) return false;
    m_writeError.clear();
    m_app.windowButtons = choice == QLatin1String("theme") ? QString() : choice;
    showCurrent(false);
    if (m_o.role == Role::Session) {
        if (!AppearanceFile::write(m_o.appearancePath, m_app, &m_writeError)) {
            emit announce(tr("Could not save the setting: %1").arg(m_writeError));
            return false;
        }
        writeMirror();
        if (m_o.watch) rewatch();
    }
    emitAppearance();
    return true;
}

bool ThemeManager::setTheme(const QString &id) { return choose(id, m_app.mode); }
bool ThemeManager::setMode(const QString &mode) { return choose(m_shownId.isEmpty() ? m_app.theme : m_shownId, mode); }
