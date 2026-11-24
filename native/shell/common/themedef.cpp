#include "themedef.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>

#include "contrast.h"

namespace ThemeTokens {
static const char *const kNames[Count] = {
    "ground", "surface", "raised", "stroke", "hairline", "text", "textSecondary", "textTertiary", "accent",
    "accentText", "warning", "danger", "ok", "series1", "series2", "selection", "focusRing", "live", "track",
    "scrollbar"};
const char *name(int t) { return t >= 0 && t < Count ? kNames[t] : ""; }
int indexOf(const QString &n) {
    for (int i = 0; i < Count; ++i)
        if (n == QLatin1String(kNames[i])) return i;
    return -1;
}
}  // namespace ThemeTokens

QColor parseCssColor(const QString &s) {
    static const QRegularExpression re(QStringLiteral("^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$"));
    if (!re.match(s).hasMatch()) return QColor();
    const uint v = s.mid(1).toUInt(nullptr, 16);
    if (s.size() == 9) return QColor::fromRgb(int((v >> 24) & 0xff), int((v >> 16) & 0xff), int((v >> 8) & 0xff), int(v & 0xff));
    return QColor::fromRgb(int((v >> 16) & 0xff), int((v >> 8) & 0xff), int(v & 0xff));
}

QString toCssColor(const QColor &c) {
    QString s = QStringLiteral("#%1%2%3").arg(c.red(), 2, 16, QLatin1Char('0')).arg(c.green(), 2, 16, QLatin1Char('0')).arg(c.blue(), 2, 16, QLatin1Char('0'));
    if (c.alpha() != 255) s += QStringLiteral("%1").arg(c.alpha(), 2, 16, QLatin1Char('0'));
    return s.toUpper();
}

bool validThemeId(const QString &id) {
    static const QRegularExpression re(QStringLiteral("^[a-z0-9][a-z0-9-]{0,63}$"));
    return re.match(id).hasMatch();
}

bool validButtonSide(const QString &side) { return side == QLatin1String("left") || side == QLatin1String("right"); }

ThemeParse parseTheme(const QJsonObject &doc, const QString &expectedId, const QString &source) {
    ThemeParse r;
    ThemeDef &d = r.def;
    d.source = source;
    d.id = doc.value(QStringLiteral("id")).toString();
    if (!validThemeId(d.id)) r.errors << QStringLiteral("the id must be lowercase letters, digits and dashes");
    else if (!expectedId.isEmpty() && d.id != expectedId)
        r.errors << QStringLiteral("the id '%1' does not match its folder name '%2'").arg(d.id, expectedId);
    d.name = doc.value(QStringLiteral("name")).toString().trimmed();
    if (d.name.isEmpty()) r.errors << QStringLiteral("it has no display name");
    d.description = doc.value(QStringLiteral("description")).toString().trimmed();
    for (const QJsonValue &v : doc.value(QStringLiteral("variants")).toArray()) {
        const QString s = v.toString();
        if ((s == QLatin1String("dark") || s == QLatin1String("light")) && !d.variants.contains(s)) d.variants << s;
        else if (s != QLatin1String("dark") && s != QLatin1String("light")) r.errors << QStringLiteral("unknown variant '%1'").arg(s);
    }
    if (d.variants.isEmpty()) r.errors << QStringLiteral("it lists no variant (dark or light)");
    if (doc.contains(QStringLiteral("windowButtons"))) {
        const QString side = doc.value(QStringLiteral("windowButtons")).toString();
        if (validButtonSide(side)) d.windowButtons = side;
        else r.errors << QStringLiteral("windowButtons must be \"left\" or \"right\"");
    }
    d.reduceMotion = doc.value(QStringLiteral("motion")).toObject().value(QStringLiteral("reduce")).toBool(false);

    const QJsonObject cols = doc.value(QStringLiteral("colors")).toObject();
    for (const QString &var : std::as_const(d.variants)) {
        const QJsonObject o = cols.value(var).toObject();
        if (o.isEmpty()) {
            r.errors << QStringLiteral("no colours for the %1 variant").arg(var);
            continue;
        }
        TokenSet ts;
        bool complete = true;
        for (int t = 0; t < ThemeTokens::Count; ++t) {
            const QString key = QString::fromLatin1(ThemeTokens::name(t));
            if (!o.contains(key)) {
                r.errors << QStringLiteral("%1: the colour '%2' is missing").arg(var, key);
                complete = false;
                continue;
            }
            const QColor c = parseCssColor(o.value(key).toString());
            if (!c.isValid()) {
                r.errors << QStringLiteral("%1: '%2' is not a hex colour like #RRGGBB or #RRGGBBAA").arg(var, key);
                complete = false;
                continue;
            }
            ts[t] = c;
        }
        if (!complete) continue;
        d.colors.insert(var, ts);
        auto check = [&](int fg, int bg, const char *what) {
            const double ratio = Contrast::ratio(Contrast::over(ts[fg], Contrast::over(ts[bg], ts[ThemeTokens::Ground])),
                                                 Contrast::over(ts[bg], ts[ThemeTokens::Ground]));
            if (ratio < kMinContrast)
                r.errors << QStringLiteral("%1: %2 has a contrast of %3:1, at least 4.5:1 is needed").arg(var, QString::fromLatin1(what)).arg(ratio, 0, 'f', 2);
        };
        check(ThemeTokens::Text, ThemeTokens::Surface, "text on surface");
        check(ThemeTokens::Text, ThemeTokens::Ground, "text on ground");
        check(ThemeTokens::AccentText, ThemeTokens::Accent, "accentText on accent");
        // Swatches: the file's, else ground, surface and accent.
        QVector<QColor> sw;
        const QJsonArray arr = doc.value(QStringLiteral("swatches")).toObject().value(var).toArray();
        for (const QJsonValue &v : arr) {
            const QColor c = parseCssColor(v.toString());
            if (c.isValid()) sw << c;
        }
        if (sw.size() != 3) sw = {ts[ThemeTokens::Ground], ts[ThemeTokens::Surface], ts[ThemeTokens::Accent]};
        d.swatches.insert(var, sw);
    }
    r.ok = r.errors.isEmpty();
    return r;
}

ThemeParse parseThemeFile(const QString &path, const QString &expectedId) {
    ThemeParse r;
    r.def.source = path;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        r.errors << QStringLiteral("the file cannot be read");
        return r;
    }
    QJsonParseError pe;
    const QJsonDocument jd = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !jd.isObject()) {
        r.errors << QStringLiteral("not valid JSON (%1)").arg(pe.errorString());
        return r;
    }
    return parseTheme(jd.object(), expectedId, path);
}
