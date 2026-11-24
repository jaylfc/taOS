#pragma once
#include <QString>
#include <QStringList>
#include <QVector>

#include "themedef.h"

// The themes a person can choose from. Sources, first match wins per id:
//   1. the themes shipped inside the binary (":/taos/themes"), so a third-party theme can
//      never take the place of a shipped one;
//   2. $TAOS_THEME_DIR (development and tests);
//   3. the person's themes: ~/.local/share/taos/themes (not read by the greeter);
//   4. the system themes: /usr/share/taos/themes.
// A theme that fails validation is not listed; it is reported in refused() with the reason.
class ThemeCatalog {
public:
    struct Refused {
        QString id;       // the folder name
        QString source;   // path to its theme.json
        QString message;  // plain sentences
    };
    static constexpr const char *kDefaultId = "graphite";
    static QString builtinDir() { return QStringLiteral(":/taos/themes"); }
    static QString userDir();     // $XDG_DATA_HOME/taos/themes or ~/.local/share/taos/themes
    static QString systemDir();   // $TAOS_SYSTEM_THEME_DIR or /usr/share/taos/themes
    // The directories in precedence order (after the built-in ones). includeUser=false for the greeter.
    static QStringList standardDirs(bool includeUser);

    // `extraDirs` come before `dirs`' user and system entries; the built-in directory is always first.
    void scan(const QStringList &dirs, const QString &builtin = builtinDir());
    const QVector<ThemeDef> &themes() const { return m_themes; }
    const QVector<Refused> &refused() const { return m_refused; }
    const ThemeDef *find(const QString &id) const;
    // The theme to show for a saved id: it, else the default, else any theme at all.
    const ThemeDef *resolve(const QString &id) const;

private:
    QVector<ThemeDef> m_themes;
    QVector<Refused> m_refused;
};
