// SPDX-License-Identifier: AGPL-3.0-or-later
#include "theme.h"

#include <QEasingCurve>
#include <QFileInfo>
#include <QFontDatabase>
#include <QGuiApplication>
#include <cstdio>

#include "themecatalog.h"

Theme::Theme(QObject *parent) : QObject(parent) {
    m_anim.setStartValue(0.0);
    m_anim.setEndValue(1.0);
    m_anim.setEasingCurve(QEasingCurve::OutCubic);
    connect(&m_anim, &QVariantAnimation::valueChanged, this, [this](const QVariant &v) { setCurrent(v.toDouble()); });
    connect(&m_anim, &QVariantAnimation::finished, this, [this] { setCurrent(1.0); });
}

static QColor lerp(const QColor &a, const QColor &b, double t) {
    return QColor::fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                            a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t);
}

void Theme::setCurrent(double t) {
    for (int i = 0; i < ThemeTokens::Count; ++i) m_cur[i] = t >= 1.0 ? m_to[i] : lerp(m_from[i], m_to[i], t);
    emit changed();
}

void Theme::setWindowButtonsOverride(const QString &side) {
    const QString before = windowButtons();
    m_buttonsOverride = validButtonSide(side) ? side : QString();
    if (windowButtons() != before) emit changed();
}

QString Theme::fontStack() const {
    static const QString shipped = QStringLiteral("Inter, Noto Sans, DejaVu Sans, sans-serif");
    return m_skin.fontFamily.isEmpty() ? shipped : m_skin.fontFamily + QStringLiteral(", ") + shipped;
}

QString Theme::fontDisplayStack() const { return m_skin.fontDisplay.isEmpty() ? fontStack() : m_skin.fontDisplay + QStringLiteral(", ") + fontStack(); }
QString Theme::fontDataStack() const { return m_skin.fontData.isEmpty() ? fontStack() : m_skin.fontData + QStringLiteral(", ") + fontStack(); }

qreal Theme::spacingFactor() const {
    if (m_skin.density == QLatin1String("compact")) return 0.75;
    if (m_skin.density == QLatin1String("comfortable")) return 1.25;
    return 1.0;
}

int Theme::trayWidth() const {
    const int item = m_layout.taskbarHeight - 4;                       // one square tray button
    const int clock = qRound(m_skin.fontSize * 3.6) + int(space(20));   // "HH:mm" in the tabular face, with its padding
    return 3 * item + clock + int(space(10)) * 2;
}

QColor Theme::bevelLight() const {   // raised, lifted towards white
    const QColor r = raised();
    return QColor::fromRgbF(r.redF() + (1 - r.redF()) * 0.75, r.greenF() + (1 - r.greenF()) * 0.75, r.blueF() + (1 - r.blueF()) * 0.75);
}
QColor Theme::bevelDark() const {    // the text colour at 45 %, over the surface
    const QColor t = text(), s = surface();
    return QColor::fromRgbF(s.redF() * 0.55 + t.redF() * 0.45, s.greenF() * 0.55 + t.greenF() * 0.45, s.blueF() * 0.55 + t.blueF() * 0.45);
}

bool Theme::apply(const ThemeDef &def, const QString &variantIn, bool reduceMotionFlag) {
    QString v = variantIn == QLatin1String("light") ? QStringLiteral("light") : QStringLiteral("dark");
    if (!def.colors.contains(v)) v = def.colors.contains(QStringLiteral("dark")) ? QStringLiteral("dark") : QStringLiteral("light");
    if (!def.colors.contains(v)) return false;
    const TokenSet next = def.colors.value(v);
    const bool reduce = reduceMotionFlag || def.reduceMotion;
    const bool animate = m_have && !reduce && m_transitionMs > 0;
    const QString oldButtons = windowButtons();
    m_themeButtons = validButtonSide(def.windowButtons) ? def.windowButtons : QStringLiteral("left");
    QString wallpaper;
    if (!def.skin.wallpaper.isEmpty() && !def.builtin && !def.source.startsWith(QLatin1Char(':')))
        wallpaper = QFileInfo(def.source).absolutePath() + QLatin1Char('/') + def.skin.wallpaper;
    // fonts the theme ships: loaded for this process (once per file), so the text follows the switch with no install
    if (qGuiApp && !def.builtin && !def.source.startsWith(QLatin1Char(':')))
        for (const QString &f : def.skin.fonts) {
            const QString path = QFileInfo(def.source).absolutePath() + QStringLiteral("/fonts/") + f;
            if (!m_fontFiles.contains(path)) {
                m_fontFiles.insert(path);
                if (QFontDatabase::addApplicationFont(path) < 0) fprintf(stderr, "taos-theme: cannot load the font %s\n", qPrintable(path));
            }
        }
    const bool skinDiffers = !(m_skin == def.skin) || m_wallpaper != wallpaper;
    const bool layoutDiffers = !(m_layout == def.layout);
    const bool sideChanged = windowButtons() != oldButtons;
    m_skin = def.skin;
    m_layout = def.layout;
    m_wallpaper = wallpaper;
    bool same = m_have && m_id == def.id && m_variant == v && m_reduce == reduce && m_source == def.source;
    for (int i = 0; same && i < ThemeTokens::Count; ++i) same = m_to[i] == next[i];
    if (same) {
        if (sideChanged) emit changed();   // only the button side changed (another theme with the same colours)
        if (skinDiffers) emit skinChanged();
        if (layoutDiffers) emit layoutChanged();
        if (!sideChanged && (skinDiffers || layoutDiffers)) emit changed();
        return true;  // nothing else to change: no fade
    }
    m_anim.stop();
    m_from = m_have ? m_cur : next;
    m_to = next;
    m_id = def.id;
    m_name = def.name;
    m_variant = v;
    m_source = def.source;
    m_reduce = reduce;
    m_have = true;
    if (animate) {
        m_anim.setDuration(m_transitionMs);
        m_anim.start();
        setCurrent(0.0);
    } else {
        setCurrent(1.0);
    }
    if (skinDiffers) emit skinChanged();
    if (layoutDiffers) emit layoutChanged();
    return true;
}

bool Theme::load(const QString &id, const QString &variant, const QStringList &searchDirs,
                 const QString &builtinPath, bool reduceMotionFlag, QString *error) {
    for (const QString &d : searchDirs) {
        if (d.isEmpty()) continue;
        const ThemeParse p = parseThemeFile(d + QLatin1Char('/') + id + QStringLiteral("/theme.json"), id);
        if (p.ok) return apply(p.def, variant, reduceMotionFlag);
    }
    const ThemeParse p = parseThemeFile(builtinPath, QString());
    if (!p.ok) {
        if (error) *error = QStringLiteral("no usable theme '%1' and the fallback theme is unusable: %2").arg(id, p.message());
        return false;
    }
    return apply(p.def, variant, reduceMotionFlag);
}

QColor Theme::barBackground() const { QColor c = ground(); c.setAlphaF(kBarOpacity); return c; }
QColor Theme::hover() const { QColor c = text(); c.setAlphaF(0.10); return c; }
QColor Theme::pressed() const { QColor c = text(); c.setAlphaF(0.18); return c; }
QColor Theme::glassTint() const { QColor c = ground(); c.setAlphaF(dark() ? 0.52 : 0.62); return c; }
QColor Theme::glassVeil() const {
    QColor c = text();  // a faint light that makes dark glass read as glass (up to 11%, fading to 4% down the panel)
    c.setAlphaF(dark() ? 0.11 : 0.0);
    return c;
}
QColor Theme::glassStroke() const { QColor c = text(); c.setAlphaF(dark() ? 0.24 : 0.30); return c; }
QColor Theme::glassHighlight() const {  // the one pixel specular line along the top edge of glass
    QColor c = dark() ? text() : raised();
    c.setAlphaF(dark() ? 0.22 : 0.80);
    return c;
}
QColor Theme::scrim() const { QColor c = ground(); c.setAlphaF(dark() ? 0.35 : 0.55); return c; }  // darkening (dark) or lightening (light) behind bare text
