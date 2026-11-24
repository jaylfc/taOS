#pragma once
#include <QDateTime>
#include <QFileSystemWatcher>
#include <QObject>
#include <QTimer>
#include <QVariantList>
#include <functional>

#include "appearance.h"
#include "theme.h"
#include "themecatalog.h"

// ThemeManager: the choice of theme and mode, live. It reads appearance.json, watches it (and
// its folder, and the theme folders), resolves "auto" against the clock, and drives a Theme so
// every component bound to that Theme recolours at once. Also the model of the picker.
//
// Roles:
//  - Session: a person's session. Reads ~/.config/taos/appearance.json, writes it on a choice,
//    and keeps a world-readable copy at <mirrorDir>/<uid>.json when that folder exists and is
//    writable, so the greeter can show this person's choice before they log in.
//  - Greeter: runs before login as another user. Reads the system default (/etc/taos/appearance.json,
//    else graphite dark) and, per selected person, only <mirrorDir>/<uid>.json. Never reads a home folder.
class ThemeManager : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString themeId READ themeId NOTIFY appearanceChanged)
    Q_PROPERTY(QString themeName READ themeName NOTIFY appearanceChanged)
    Q_PROPERTY(QString mode READ mode NOTIFY appearanceChanged)
    Q_PROPERTY(QString variant READ variant NOTIFY appearanceChanged)
    Q_PROPERTY(QVariantList themes READ themes NOTIFY themesChanged)
    Q_PROPERTY(QVariantList modes READ modes NOTIFY appearanceChanged)
    Q_PROPERTY(QString description READ description NOTIFY appearanceChanged)
    Q_PROPERTY(QString windowButtons READ windowButtons NOTIFY appearanceChanged)
    Q_PROPERTY(QString windowButtonsChoice READ windowButtonsChoice NOTIFY appearanceChanged)
public:
    enum class Role { Session, Greeter };
    struct Options {
        Role role = Role::Session;
        QString appearancePath;                 // default: AppearanceFile::userPath()
        QString systemPath;                     // default: AppearanceFile::systemPath()
        QString mirrorDir;                      // default: AppearanceFile::mirrorDir()
        QStringList themeDirs;                  // default: ThemeCatalog::standardDirs(role == Session)
        QString builtinDir;                     // default: ThemeCatalog::builtinDir()
        QString pinnedTheme;                    // set: wins over the file (command line, screenshots)
        QString pinnedVariant;                  // "dark" | "light" | "auto": wins over the file
        bool reduceMotion = false;
        int transitionMs = 150;
        bool watch = true;
        int uid = -1;                           // session: the mirror's name; default getuid()
        std::function<QDateTime()> clock;       // default: the system clock
    };
    explicit ThemeManager(Theme *theme, const Options &o, QObject *parent = nullptr);

    // Reads everything and shows it. Returns false only when no theme at all is usable.
    bool start(QString *error = nullptr);

    QString themeId() const { return m_shownId; }
    QString themeName() const;
    QString mode() const { return m_app.mode; }
    QString variant() const { return m_variant; }
    QString description() const;
    const Appearance &requested() const { return m_app; }
    QVariantList themes() const;     // [{id,name,description,swatches:[css,css,css],current}]
    QVariantList modes() const;      // [{id,label,current}] dark, light, auto
    const ThemeCatalog &catalog() const { return m_cat; }
    // "Theme: Graphite + Ice, dark" (auto: "Theme: Graphite, auto, light now").
    QString announcementText() const;

    // Picker actions: apply live and save. False (with `error`) when the theme is unknown or the file cannot be written.
    Q_INVOKABLE bool setTheme(const QString &id);
    Q_INVOKABLE bool setMode(const QString &mode);
    // The side of the window buttons: "left", "right" or "theme" (follow the theme's windowButtons). Live and saved.
    Q_INVOKABLE bool setWindowButtons(const QString &choice);
    QString windowButtons() const { return m_theme->windowButtons(); }                 // the side in effect
    QString windowButtonsChoice() const { return m_app.windowButtons.isEmpty() ? QStringLiteral("theme") : m_app.windowButtons; }
    Q_INVOKABLE bool choose(const QString &id, const QString &mode);

    // Greeter: show this person's choice (their mirror file), else the system default.
    void previewUser(int uid);
    // Re-read the files and the clock now (also what the watcher and the minute timer call).
    void reload();
    void rescanThemes();
    QString lastWriteError() const { return m_writeError; }

signals:
    void appearanceChanged();
    void themesChanged();
    void announce(const QString &text);   // spoken by the picker after a choice

private:
    Appearance readSource() const;
    void showCurrent(bool announceIt);
    void rewatch();
    void emitAppearance();
    QString mirrorPath() const;
    void writeMirror();
    QDateTime now() const { return m_o.clock ? m_o.clock() : QDateTime::currentDateTime(); }

    Theme *m_theme;
    Options m_o;
    ThemeCatalog m_cat;
    Appearance m_app;
    QString m_shownId, m_variant = QStringLiteral("dark"), m_writeError;
    int m_previewUid = -1;
    QFileSystemWatcher m_watch;
    QTimer m_debounce, m_tick;
    bool m_started = false;
};
