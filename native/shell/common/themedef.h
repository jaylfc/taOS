// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QColor>
#include <QJsonObject>
#include <QMap>
#include <QString>
#include <QStringList>
#include <QVector>

// The theme package format: `<dir>/<id>/theme.json`. Every colour token below
// is required for every variant a theme lists; see THEMES.md.
namespace ThemeTokens {
enum Token {
    Ground, Surface, Raised, Stroke, Hairline, Text, TextSecondary, TextTertiary, Accent, AccentText,
    Warning, Danger, Ok, Series1, Series2, Selection, FocusRing, Live, Track, Scrollbar,
    Attention, AccentDim,     // optional: absent means accent and stroke
    Count
};
const char *name(int t);
int indexOf(const QString &name);  // -1 when unknown
bool optional(int t);              // Attention and AccentDim may be left out of a theme
}  // namespace ThemeTokens

struct TokenSet {
    QColor c[ThemeTokens::Count];
    const QColor &operator[](int t) const { return c[t]; }
    QColor &operator[](int t) { return c[t]; }
};

// "#RRGGBB" or "#RRGGBBAA" (CSS order); invalid colour on anything else.
QColor parseCssColor(const QString &s);
QString toCssColor(const QColor &c);

// Optional `skin` section: how the shell pieces are drawn. Every key is optional; an absent key (or an absent
// section) keeps the value below, which is exactly what the shell drew before skins existed. See THEME-FORMAT.md.
struct Skin {
    double radiusScale = 1.0;                 // 0..2, multiplies every corner radius the shell pieces draw
    int borderWidth = 1;                      // 0..4 px, hairlines of bars, dock, menus, launcher, title bars
    double shadowStrength = 1.0;              // 0..2, multiplies the drop shadows of menus and the launcher
    QString fontFamily;                       // "" = the shipped stack (Inter, Noto Sans, DejaVu Sans, sans-serif)
    QString fontDisplay;                      // "" = the body face: titles and labels of the bar and the taskbar
    QString fontData;                         // "" = the body face: numbers, readouts, small labels
    QStringList fonts;                        // font files bundled in the theme folder (fonts/<name>.ttf|otf), loaded when the theme shows
    QString titleCase = QStringLiteral("none");     // none | upper: window titles drawn by the compositor and labels of the bar
    double titleTracking = 0;                 // 0..0.4 em of extra space between letters in titles and bar labels
    QString focusBorder = QStringLiteral("neutral"); // neutral | accent: the 1 px border of framed windows
    bool brackets = false;                    // corner brackets on the focused window (taos-frame overlay)
    bool blur = false;                        // ask the compositor to blur behind shell surfaces (swayfx; plain sway ignores it)
    QString texture = QStringLiteral("none"); // none | scanlines: a faint line texture on bar, dock and launcher surfaces
    int fontSize = 13;                        // 9..20 px, the base size of bar, taskbar and title bar text
    double iconStroke = 1.5;                  // 0.75..3 px, stroke of the bar and taskbar glyphs
    int titleBarHeight = 32;                  // 20..48 px, window title bars drawn by the compositor (taos-frame)
    QString buttonShape = QStringLiteral("round");   // round | square | glyph
    int buttonSize = 14;                      // 10..24 px, the visible disc or square of a window button
    QString activeTitle = QStringLiteral("surface");   // surface | accent: the colour of the focused window's title bar
    QStringList buttonOrder;                  // [] = by side; else close, minimise and full in on-screen order, left to right
    QString barSurface = QStringLiteral("translucent");    // solid | translucent | raised
    QString dockSurface = QStringLiteral("translucent");   // solid | translucent | raised (the dock and the taskbar)
    QString density = QStringLiteral("regular");           // compact | regular | comfortable
    QString titleAlign = QStringLiteral("center");    // center | left: the window title in the compositor drawn title bar
    int windowRadius = 0;                     // 0..32 px, corner radius of windows (compositor, swayfx only)
    int windowGap = 8;                        // 0..32 px, gap between tiled windows (compositor)
    QString wallpaper;                        // "" or a png/jpg file name inside the theme folder
    bool operator==(const Skin &o) const {
        return fontDisplay == o.fontDisplay && fontData == o.fontData && fonts == o.fonts && titleCase == o.titleCase
            && titleTracking == o.titleTracking && focusBorder == o.focusBorder && brackets == o.brackets && blur == o.blur
            && texture == o.texture && radiusScale == o.radiusScale && borderWidth == o.borderWidth && shadowStrength == o.shadowStrength
            && fontFamily == o.fontFamily && fontSize == o.fontSize && iconStroke == o.iconStroke
            && titleBarHeight == o.titleBarHeight && buttonShape == o.buttonShape && buttonSize == o.buttonSize
            && buttonOrder == o.buttonOrder && activeTitle == o.activeTitle && titleAlign == o.titleAlign && barSurface == o.barSurface && dockSurface == o.dockSurface
            && density == o.density && windowRadius == o.windowRadius && windowGap == o.windowGap && wallpaper == o.wallpaper;
    }
};

// Optional `layout` section: which shell pieces exist and where they sit. Same rule: absent means today's shell.
struct LayoutSpec {
    bool topBar = true;                       // the top status bar exists
    int barHeight = 40;                       // 24..64 px
    QString barEdge = QStringLiteral("top");  // top | bottom
    QString clock = QStringLiteral("right");  // left | center | right, in the top bar (in the taskbar the clock is in the tray)
    QString barStyle = QStringLiteral("bar"); // bar | strip: the top bar as a thin instrument strip (marks, workspace ticks, tick meter volume)
    QString dock = QStringLiteral("dock");    // dock (floating, centred) | taskbar (full-width strip with start button, windows, tray) | cells (a row of shaped cells)
    QString cellShape = QStringLiteral("hex");     // hex | square: the cells of dock "cells"
    int taskbarHeight = 40;                   // 28..64 px
    QString dockEdge = QStringLiteral("bottom");   // top | bottom (the taskbar; the floating dock keeps dock.json)
    QString launcher = QStringLiteral("centered"); // centered | anchored (a compact menu at the taskbar's start button) | ring (a wide one line field above the dock)
    QString startLabel = QStringLiteral("taOS");   // text of the taskbar's start button, up to 16 letters, digits and spaces
    bool operator==(const LayoutSpec &o) const {
        return barStyle == o.barStyle && cellShape == o.cellShape && topBar == o.topBar && barHeight == o.barHeight && barEdge == o.barEdge && clock == o.clock && dock == o.dock
            && taskbarHeight == o.taskbarHeight && dockEdge == o.dockEdge && launcher == o.launcher && startLabel == o.startLabel;
    }
};

// A choice the person can make in a theme (the accent colour of Parallax, say): each choice may replace some colour
// tokens and the wallpaper, nothing else. Data only.
struct ThemeChoice {
    QString id, label, note;
    QMap<QString, QString> colors;   // token name -> "#RRGGBB[AA]" (applied to every variant)
    QString wallpaper;               // "" keeps the skin's
    bool available = true;           // false: the choice is accepted and saved but the system cannot show it yet
};
struct ThemeOption {
    QString key, label, defaultChoice;
    QVector<ThemeChoice> choices;
    const ThemeChoice *find(const QString &id) const { for (const ThemeChoice &c : choices) if (c.id == id) return &c; return nullptr; }
};

struct ThemeDef {
    QString id;
    QString name;
    QString description;
    QString source;               // file path, or ":/..." for a built-in theme
    bool builtin = false;
    bool reduceMotion = false;
    Skin skin;
    LayoutSpec layout;
    QVector<ThemeOption> options;
    bool windowButtonsFromTop = false;   // parse detail: the top level key was given
    QString windowButtons = QStringLiteral("left");   // "left" (close, minimise, full size) or "right" (minimise, full size, close)
    QStringList variants;          // "dark" and/or "light"
    QMap<QString, TokenSet> colors;       // per variant
    QMap<QString, QVector<QColor>> swatches;  // per variant: ground, surface, accent
    bool hasVariant(const QString &v) const { return variants.contains(v); }
};

struct ThemeParse {
    bool ok = false;
    ThemeDef def;
    QStringList errors;            // plain sentences, one problem each
    QString message() const { return errors.join(QStringLiteral("; ")); }
};

// Validate and read a theme document. `expectedId` is the directory name (empty: no check).
// Refused when: the id is malformed or differs from the directory, the name is missing,
// a token is missing or is not a hex colour, or text on surface (or on ground) or
// accentText on accent is under 4.5:1.
ThemeParse parseTheme(const QJsonObject &doc, const QString &expectedId, const QString &source);
ThemeParse parseThemeFile(const QString &path, const QString &expectedId);
// The theme as shown with the person's choices: `chosen` maps option key to choice id. An unknown key or choice is
// ignored (the default shows); a choice that is not available shows the default and is named in `notes`.
ThemeDef applyOptions(const ThemeDef &def, const QMap<QString, QString> &chosen, QStringList *notes = nullptr);
bool validThemeId(const QString &id);
bool validButtonSide(const QString &side);   // "left" or "right"
// The window button order on screen, left to right, for a side when the skin gives none.
QStringList defaultButtonOrder(const QString &side);
// A font family name a skin may use: letters, digits, spaces and dashes, up to 48 characters.
bool validFontFamily(const QString &f);
constexpr double kMinContrast = 4.5;
