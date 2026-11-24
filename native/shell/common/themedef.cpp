// SPDX-License-Identifier: AGPL-3.0-or-later
#include "themedef.h"

#include <QFile>
#include <QFileInfo>
#include <cmath>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>

#include "contrast.h"

namespace ThemeTokens {
static const char *const kNames[Count] = {
    "ground", "surface", "raised", "stroke", "hairline", "text", "textSecondary", "textTertiary", "accent",
    "accentText", "warning", "danger", "ok", "series1", "series2", "selection", "focusRing", "live", "track",
    "scrollbar", "attention", "accentDim"};
const char *name(int t) { return t >= 0 && t < Count ? kNames[t] : ""; }
bool optional(int t) { return t == Attention || t == AccentDim; }
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

bool validFontFamily(const QString &f) {
    static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9 -]{0,47}$"));
    return re.match(f).hasMatch();
}

QStringList defaultButtonOrder(const QString &side) {
    if (side == QLatin1String("right")) return {QStringLiteral("minimise"), QStringLiteral("full"), QStringLiteral("close")};
    return {QStringLiteral("close"), QStringLiteral("minimise"), QStringLiteral("full")};
}

namespace {
// Strict readers for the skin and layout sections: a wrong type, an out of range number or an unknown key is
// one plain sentence in `errors`, and the section is refused as a whole (the theme is refused).
struct Section {
    const QJsonObject &o;
    const QString where;
    QStringList &errors;
    void rejectUnknown(const QStringList &known) {
        for (auto it = o.begin(); it != o.end(); ++it)
            if (!known.contains(it.key()))
                errors << QStringLiteral("%1: unknown key '%2' (known: %3)").arg(where, it.key(), known.join(QStringLiteral(", ")));
    }
    bool number(const QString &key, double lo, double hi, double *out, bool integer) {
        if (!o.contains(key)) return false;
        const QJsonValue v = o.value(key);
        if (!v.isDouble()) { errors << QStringLiteral("%1.%2 must be a number").arg(where, key); return false; }
        const double d = v.toDouble();
        if (integer && d != std::floor(d)) { errors << QStringLiteral("%1.%2 must be a whole number").arg(where, key); return false; }
        if (!(d >= lo && d <= hi)) {
            errors << QStringLiteral("%1.%2 is %3, it must be between %4 and %5").arg(where, key).arg(d).arg(lo).arg(hi);
            return false;
        }
        *out = d;
        return true;
    }
    void intKey(const QString &key, int lo, int hi, int *out) { double d; if (number(key, lo, hi, &d, true)) *out = int(d); }
    void realKey(const QString &key, double lo, double hi, double *out) { double d; if (number(key, lo, hi, &d, false)) *out = d; }
    void enumKey(const QString &key, const QStringList &allowed, QString *out) {
        if (!o.contains(key)) return;
        const QJsonValue v = o.value(key);
        if (!v.isString() || !allowed.contains(v.toString())) {
            errors << QStringLiteral("%1.%2 must be one of: %3").arg(where, key, allowed.join(QStringLiteral(", ")));
            return;
        }
        *out = v.toString();
    }
    void boolKey(const QString &key, bool *out) {
        if (!o.contains(key)) return;
        if (!o.value(key).isBool()) { errors << QStringLiteral("%1.%2 must be true or false").arg(where, key); return; }
        *out = o.value(key).toBool();
    }
};

void parseSkin(const QJsonObject &o, ThemeDef *d, QStringList &errors) {
    Section s{o, QStringLiteral("skin"), errors};
    s.rejectUnknown({QStringLiteral("radiusScale"), QStringLiteral("borderWidth"), QStringLiteral("shadowStrength"),
                     QStringLiteral("fontFamily"), QStringLiteral("fontSize"), QStringLiteral("iconStroke"),
                     QStringLiteral("titleBarHeight"), QStringLiteral("windowButtons"), QStringLiteral("barSurface"),
                     QStringLiteral("dockSurface"), QStringLiteral("activeTitle"), QStringLiteral("titleAlign"), QStringLiteral("density"), QStringLiteral("windowRadius"),
                     QStringLiteral("windowGap"), QStringLiteral("wallpaper"), QStringLiteral("fontDisplay"), QStringLiteral("fontData"),
                     QStringLiteral("fonts"), QStringLiteral("titleCase"), QStringLiteral("titleTracking"), QStringLiteral("focusBorder"),
                     QStringLiteral("brackets"), QStringLiteral("blur"), QStringLiteral("texture")});
    Skin &k = d->skin;
    s.realKey(QStringLiteral("radiusScale"), 0, 2, &k.radiusScale);
    s.intKey(QStringLiteral("borderWidth"), 0, 4, &k.borderWidth);
    s.realKey(QStringLiteral("shadowStrength"), 0, 2, &k.shadowStrength);
    s.intKey(QStringLiteral("fontSize"), 9, 20, &k.fontSize);
    s.realKey(QStringLiteral("iconStroke"), 0.75, 3, &k.iconStroke);
    s.intKey(QStringLiteral("titleBarHeight"), 20, 48, &k.titleBarHeight);
    s.enumKey(QStringLiteral("barSurface"), {QStringLiteral("solid"), QStringLiteral("translucent"), QStringLiteral("raised")}, &k.barSurface);
    s.enumKey(QStringLiteral("dockSurface"), {QStringLiteral("solid"), QStringLiteral("translucent"), QStringLiteral("raised")}, &k.dockSurface);
    s.enumKey(QStringLiteral("activeTitle"), {QStringLiteral("surface"), QStringLiteral("accent")}, &k.activeTitle);
    s.enumKey(QStringLiteral("titleCase"), {QStringLiteral("none"), QStringLiteral("upper")}, &k.titleCase);
    s.realKey(QStringLiteral("titleTracking"), 0, 0.4, &k.titleTracking);
    s.enumKey(QStringLiteral("focusBorder"), {QStringLiteral("neutral"), QStringLiteral("accent")}, &k.focusBorder);
    s.enumKey(QStringLiteral("texture"), {QStringLiteral("none"), QStringLiteral("scanlines")}, &k.texture);
    s.boolKey(QStringLiteral("brackets"), &k.brackets);
    s.boolKey(QStringLiteral("blur"), &k.blur);
    for (const char *fk : {"fontDisplay", "fontData"}) {
        const QString key = QString::fromLatin1(fk);
        if (!o.contains(key)) continue;
        const QJsonValue v = o.value(key);
        if (!v.isString() || !validFontFamily(v.toString()))
            errors << QStringLiteral("skin.%1 must be one font family name: letters, digits, spaces and dashes, up to 48 characters").arg(key);
        else (key == QLatin1String("fontDisplay") ? k.fontDisplay : k.fontData) = v.toString();
    }
    if (o.contains(QStringLiteral("fonts"))) {
        static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\\.(ttf|otf)$"));
        const QJsonValue v = o.value(QStringLiteral("fonts"));
        bool ok = v.isArray() && v.toArray().size() <= 12;
        QStringList list;
        for (const QJsonValue &x : v.toArray()) {
            if (!x.isString() || !re.match(x.toString()).hasMatch() || x.toString().contains(QStringLiteral(".."))) ok = false;
            else list << x.toString();
        }
        if (!ok) errors << QStringLiteral("skin.fonts must list up to 12 plain file names ending in .ttf or .otf; the files sit in the theme folder's fonts folder");
        else k.fonts = list;
    }
    s.enumKey(QStringLiteral("titleAlign"), {QStringLiteral("center"), QStringLiteral("left")}, &k.titleAlign);
    s.enumKey(QStringLiteral("density"), {QStringLiteral("compact"), QStringLiteral("regular"), QStringLiteral("comfortable")}, &k.density);
    s.intKey(QStringLiteral("windowRadius"), 0, 32, &k.windowRadius);
    s.intKey(QStringLiteral("windowGap"), 0, 32, &k.windowGap);
    if (o.contains(QStringLiteral("fontFamily"))) {
        const QJsonValue v = o.value(QStringLiteral("fontFamily"));
        if (!v.isString() || !validFontFamily(v.toString()))
            errors << QStringLiteral("skin.fontFamily must be one font family name: letters, digits, spaces and dashes, up to 48 characters");
        else k.fontFamily = v.toString();
    }
    if (o.contains(QStringLiteral("wallpaper"))) {
        static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\\.(png|jpg|jpeg)$"));
        const QJsonValue v = o.value(QStringLiteral("wallpaper"));
        if (!v.isString() || !re.match(v.toString()).hasMatch() || v.toString().contains(QStringLiteral("..")))
            errors << QStringLiteral("skin.wallpaper must be a plain file name ending in .png, .jpg or .jpeg that sits in the theme folder");
        else k.wallpaper = v.toString();
    }
    if (o.contains(QStringLiteral("windowButtons"))) {
        const QJsonValue v = o.value(QStringLiteral("windowButtons"));
        if (!v.isObject()) { errors << QStringLiteral("skin.windowButtons must be an object"); return; }
        const QJsonObject b = v.toObject();
        Section bs{b, QStringLiteral("skin.windowButtons"), errors};
        bs.rejectUnknown({QStringLiteral("shape"), QStringLiteral("size"), QStringLiteral("side"), QStringLiteral("order")});
        bs.enumKey(QStringLiteral("shape"), {QStringLiteral("round"), QStringLiteral("square"), QStringLiteral("glyph")}, &k.buttonShape);
        bs.intKey(QStringLiteral("size"), 10, 24, &k.buttonSize);
        if (b.contains(QStringLiteral("side"))) {
            const QString side = b.value(QStringLiteral("side")).toString();
            if (!validButtonSide(side)) errors << QStringLiteral("skin.windowButtons.side must be \"left\" or \"right\"");
            else if (d->windowButtons != side && d->windowButtonsFromTop) errors << QStringLiteral("skin.windowButtons.side '%1' contradicts the top level windowButtons '%2'; give one of them").arg(side, d->windowButtons);
            else d->windowButtons = side;
        }
        if (b.contains(QStringLiteral("order"))) {
            QStringList ord;
            bool bad = !b.value(QStringLiteral("order")).isArray();
            for (const QJsonValue &x : b.value(QStringLiteral("order")).toArray()) ord << x.toString();
            QStringList sorted = ord;
            sorted.sort();
            if (bad || sorted != QStringList{QStringLiteral("close"), QStringLiteral("full"), QStringLiteral("minimise")})
                errors << QStringLiteral("skin.windowButtons.order must list close, minimise and full once each, left to right");
            else k.buttonOrder = ord;
        }
    }
}

void parseLayout(const QJsonObject &o, ThemeDef *d, QStringList &errors) {
    Section s{o, QStringLiteral("layout"), errors};
    s.rejectUnknown({QStringLiteral("topBar"), QStringLiteral("barHeight"), QStringLiteral("barEdge"), QStringLiteral("clock"),
                     QStringLiteral("dock"), QStringLiteral("taskbarHeight"), QStringLiteral("dockEdge"),
                     QStringLiteral("launcher"), QStringLiteral("startLabel"), QStringLiteral("barStyle"), QStringLiteral("cellShape")});
    LayoutSpec &l = d->layout;
    s.boolKey(QStringLiteral("topBar"), &l.topBar);
    s.intKey(QStringLiteral("barHeight"), 24, 64, &l.barHeight);
    s.enumKey(QStringLiteral("barEdge"), {QStringLiteral("top"), QStringLiteral("bottom")}, &l.barEdge);
    s.enumKey(QStringLiteral("clock"), {QStringLiteral("left"), QStringLiteral("center"), QStringLiteral("right")}, &l.clock);
    s.enumKey(QStringLiteral("dock"), {QStringLiteral("dock"), QStringLiteral("taskbar"), QStringLiteral("cells")}, &l.dock);
    s.enumKey(QStringLiteral("barStyle"), {QStringLiteral("bar"), QStringLiteral("strip")}, &l.barStyle);
    s.enumKey(QStringLiteral("cellShape"), {QStringLiteral("hex"), QStringLiteral("square")}, &l.cellShape);
    s.intKey(QStringLiteral("taskbarHeight"), 28, 64, &l.taskbarHeight);
    s.enumKey(QStringLiteral("dockEdge"), {QStringLiteral("top"), QStringLiteral("bottom")}, &l.dockEdge);
    s.enumKey(QStringLiteral("launcher"), {QStringLiteral("centered"), QStringLiteral("anchored"), QStringLiteral("ring")}, &l.launcher);
    if (o.contains(QStringLiteral("startLabel"))) {
        static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9 ]{0,15}$"));
        const QJsonValue v = o.value(QStringLiteral("startLabel"));
        if (!v.isString() || !re.match(v.toString()).hasMatch())
            errors << QStringLiteral("layout.startLabel must be 1 to 16 letters, digits or spaces");
        else l.startLabel = v.toString();
    }
}
}  // namespace

static QStringList contrastProblems(const TokenSet &ts, const QString &var) {
    QStringList out;
    auto check = [&](int fg, int bg, const char *what) {
        const double ratio = Contrast::ratio(Contrast::over(ts[fg], Contrast::over(ts[bg], ts[ThemeTokens::Ground])),
                                             Contrast::over(ts[bg], ts[ThemeTokens::Ground]));
        if (ratio < kMinContrast)
            out << QStringLiteral("%1: %2 has a contrast of %3:1, at least 4.5:1 is needed").arg(var, QString::fromLatin1(what)).arg(ratio, 0, 'f', 2);
    };
    check(ThemeTokens::Text, ThemeTokens::Surface, "text on surface");
    check(ThemeTokens::Text, ThemeTokens::Ground, "text on ground");
    check(ThemeTokens::AccentText, ThemeTokens::Accent, "accentText on accent");
    return out;
}

static void applyChoiceColours(const ThemeChoice &ch, TokenSet *ts) {
    for (auto it = ch.colors.cbegin(); it != ch.colors.cend(); ++it) {
        const int t = ThemeTokens::indexOf(it.key());
        const QColor c = parseCssColor(it.value());
        if (t >= 0 && c.isValid()) (*ts)[t] = c;
    }
}

static void parseOptions(const QJsonValue &v, ThemeDef *d, QStringList &errors) {
    if (!v.isObject()) { errors << QStringLiteral("options must be an object"); return; }
    static const QRegularExpression keyRe(QStringLiteral("^[a-z][a-zA-Z0-9]{0,23}$"));
    const QJsonObject all = v.toObject();
    if (all.size() > 8) errors << QStringLiteral("options: at most 8 options");
    for (auto it = all.begin(); it != all.end(); ++it) {
        const QString where = QStringLiteral("options.") + it.key();
        if (!keyRe.match(it.key()).hasMatch()) { errors << QStringLiteral("%1: the key must be letters and digits, starting with a lowercase letter").arg(where); continue; }
        if (!it.value().isObject()) { errors << where + QStringLiteral(" must be an object"); continue; }
        const QJsonObject o = it.value().toObject();
        Section s{o, where, errors};
        s.rejectUnknown({QStringLiteral("label"), QStringLiteral("default"), QStringLiteral("choices")});
        ThemeOption opt;
        opt.key = it.key();
        opt.label = o.value(QStringLiteral("label")).toString().trimmed();
        if (opt.label.isEmpty() || opt.label.size() > 40) errors << where + QStringLiteral(".label must be 1 to 40 characters");
        opt.defaultChoice = o.value(QStringLiteral("default")).toString();
        const QJsonObject chs = o.value(QStringLiteral("choices")).toObject();
        if (chs.size() < 2 || chs.size() > 8) errors << where + QStringLiteral(".choices must have 2 to 8 choices");
        for (auto ci = chs.begin(); ci != chs.end(); ++ci) {
            const QString cw = where + QStringLiteral(".choices.") + ci.key();
            if (!keyRe.match(ci.key()).hasMatch() || !ci.value().isObject()) { errors << cw + QStringLiteral(" must be an object under a plain key"); continue; }
            const QJsonObject co = ci.value().toObject();
            Section cs{co, cw, errors};
            cs.rejectUnknown({QStringLiteral("label"), QStringLiteral("colors"), QStringLiteral("wallpaper"), QStringLiteral("available"), QStringLiteral("note")});
            ThemeChoice ch;
            ch.id = ci.key();
            ch.label = co.value(QStringLiteral("label")).toString().trimmed();
            if (ch.label.isEmpty() || ch.label.size() > 40) errors << cw + QStringLiteral(".label must be 1 to 40 characters");
            cs.boolKey(QStringLiteral("available"), &ch.available);
            ch.note = co.value(QStringLiteral("note")).toString().left(120);
            const QJsonObject cols = co.value(QStringLiteral("colors")).toObject();
            for (auto ki = cols.begin(); ki != cols.end(); ++ki) {
                if (ThemeTokens::indexOf(ki.key()) < 0) errors << QStringLiteral("%1.colors: unknown colour '%2'").arg(cw, ki.key());
                else if (!parseCssColor(ki.value().toString()).isValid()) errors << QStringLiteral("%1.colors.%2 is not a hex colour like #RRGGBB or #RRGGBBAA").arg(cw, ki.key());
                else ch.colors.insert(ki.key(), ki.value().toString());
            }
            if (co.contains(QStringLiteral("wallpaper"))) {
                static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\\.(png|jpg|jpeg)$"));
                const QString w = co.value(QStringLiteral("wallpaper")).toString();
                if (!re.match(w).hasMatch() || w.contains(QStringLiteral(".."))) errors << cw + QStringLiteral(".wallpaper must be a plain file name ending in .png, .jpg or .jpeg");
                else ch.wallpaper = w;
            }
            opt.choices.append(ch);
        }
        if (!opt.find(opt.defaultChoice)) errors << where + QStringLiteral(".default must name one of the choices");
        // each choice must keep the theme readable
        for (const ThemeChoice &ch : std::as_const(opt.choices))
            for (auto vi = d->colors.cbegin(); vi != d->colors.cend(); ++vi) {
                TokenSet ts = vi.value();
                applyChoiceColours(ch, &ts);
                for (const QString &p : contrastProblems(ts, vi.key()))
                    errors << QStringLiteral("%1.choices.%2: %3").arg(where, ch.id, p);
            }
        d->options.append(opt);
    }
}

ThemeDef applyOptions(const ThemeDef &def, const QMap<QString, QString> &chosen, QStringList *notes) {
    ThemeDef out = def;
    for (const ThemeOption &opt : def.options) {
        const ThemeChoice *ch = opt.find(chosen.contains(opt.key) ? chosen.value(opt.key) : opt.defaultChoice);
        if (!ch) ch = opt.find(opt.defaultChoice);
        if (ch && !ch->available) {
            if (notes) *notes << QStringLiteral("%1: %2 is not available on this system yet%3").arg(opt.label, ch->label, ch->note.isEmpty() ? QString() : QStringLiteral(" (") + ch->note + QLatin1Char(')'));
            ch = opt.find(opt.defaultChoice);
            if (ch && !ch->available) ch = nullptr;
        }
        if (!ch) continue;
        for (auto it = out.colors.begin(); it != out.colors.end(); ++it) applyChoiceColours(*ch, &it.value());
        if (!ch->wallpaper.isEmpty()) out.skin.wallpaper = ch->wallpaper;
    }
    return out;
}

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
        if (validButtonSide(side)) { d.windowButtons = side; d.windowButtonsFromTop = true; }
        else r.errors << QStringLiteral("windowButtons must be \"left\" or \"right\"");
    }
    for (const char *sec : {"skin", "layout"}) {
        const QString key = QString::fromLatin1(sec);
        if (!doc.contains(key)) continue;
        if (!doc.value(key).isObject()) { r.errors << QStringLiteral("%1 must be an object").arg(key); continue; }
        if (key == QLatin1String("skin")) parseSkin(doc.value(key).toObject(), &d, r.errors);
        else parseLayout(doc.value(key).toObject(), &d, r.errors);
    }
    if (!d.skin.fonts.isEmpty() && !source.startsWith(QLatin1Char(':')) && !source.isEmpty())
        for (const QString &f : std::as_const(d.skin.fonts))
            if (!QFile::exists(QFileInfo(source).absolutePath() + QStringLiteral("/fonts/") + f))
                r.errors << QStringLiteral("skin.fonts: the file 'fonts/%1' is not in the theme folder").arg(f);
    if (d.layout.launcher == QLatin1String("anchored") && d.layout.dock != QLatin1String("taskbar"))
        r.errors << QStringLiteral("layout.launcher \"anchored\" needs layout.dock \"taskbar\" (it opens at the start button)");
    for (const ThemeOption &opt : std::as_const(d.options))
        for (const ThemeChoice &ch : opt.choices)
            if (!ch.wallpaper.isEmpty() && !source.startsWith(QLatin1Char(':')) && !source.isEmpty() &&
                !QFile::exists(QFileInfo(source).absolutePath() + QLatin1Char('/') + ch.wallpaper))
                r.errors << QStringLiteral("options.%1.choices.%2: the wallpaper '%3' is not in the theme folder").arg(opt.key, ch.id, ch.wallpaper);
    if (!d.layout.topBar && d.layout.dock != QLatin1String("taskbar"))
        r.errors << QStringLiteral("layout: with no top bar the dock must be a taskbar (layout.dock \"taskbar\"), or the clock, volume, network and display would have no place");
    if (!d.skin.wallpaper.isEmpty() && !source.startsWith(QLatin1Char(':')) && !source.isEmpty() &&
        !QFile::exists(QFileInfo(source).absolutePath() + QLatin1Char('/') + d.skin.wallpaper))
        r.errors << QStringLiteral("skin.wallpaper: the file '%1' is not in the theme folder").arg(d.skin.wallpaper);
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
                if (ThemeTokens::optional(t)) continue;      // filled in below
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
        if (!o.contains(QStringLiteral("attention"))) ts[ThemeTokens::Attention] = ts[ThemeTokens::Accent];
        if (!o.contains(QStringLiteral("accentDim"))) ts[ThemeTokens::AccentDim] = ts[ThemeTokens::Stroke];
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
    if (doc.contains(QStringLiteral("options"))) parseOptions(doc.value(QStringLiteral("options")), &d, r.errors);
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
