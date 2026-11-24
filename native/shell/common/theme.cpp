// SPDX-License-Identifier: AGPL-3.0-or-later
#include "theme.h"

#include <QEasingCurve>

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

bool Theme::apply(const ThemeDef &def, const QString &variantIn, bool reduceMotionFlag) {
    QString v = variantIn == QLatin1String("light") ? QStringLiteral("light") : QStringLiteral("dark");
    if (!def.colors.contains(v)) v = def.colors.contains(QStringLiteral("dark")) ? QStringLiteral("dark") : QStringLiteral("light");
    if (!def.colors.contains(v)) return false;
    const TokenSet next = def.colors.value(v);
    const bool reduce = reduceMotionFlag || def.reduceMotion;
    const bool animate = m_have && !reduce && m_transitionMs > 0;
    const QString oldButtons = windowButtons();
    m_themeButtons = validButtonSide(def.windowButtons) ? def.windowButtons : QStringLiteral("left");
    bool same = m_have && m_id == def.id && m_variant == v && m_reduce == reduce && m_source == def.source;
    for (int i = 0; same && i < ThemeTokens::Count; ++i) same = m_to[i] == next[i];
    if (same) {
        if (windowButtons() != oldButtons) emit changed();   // only the button side changed (another theme with the same colours)
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
