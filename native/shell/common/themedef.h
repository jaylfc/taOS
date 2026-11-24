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
    Count
};
const char *name(int t);
int indexOf(const QString &name);  // -1 when unknown
}  // namespace ThemeTokens

struct TokenSet {
    QColor c[ThemeTokens::Count];
    const QColor &operator[](int t) const { return c[t]; }
    QColor &operator[](int t) { return c[t]; }
};

// "#RRGGBB" or "#RRGGBBAA" (CSS order); invalid colour on anything else.
QColor parseCssColor(const QString &s);
QString toCssColor(const QColor &c);

struct ThemeDef {
    QString id;
    QString name;
    QString description;
    QString source;               // file path, or ":/..." for a built-in theme
    bool builtin = false;
    bool reduceMotion = false;
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
bool validThemeId(const QString &id);
bool validButtonSide(const QString &side);   // "left" or "right"
constexpr double kMinContrast = 4.5;
