// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QColor>
#include <QObject>

#include "contrast.h"
#include "theme.h"

// The numbers behind the frosted glass and the scrims, in one place so the QML
// draws exactly what the contrast test measured. Every colour comes from the
// theme's tokens (see Theme::glass*), nothing is a literal.
struct GlassSpec {
    QColor tint;          // painted over the blurred wallpaper (alpha included)
    QColor veil;          // painted over the tint: a faint light that makes dark glass read as glass
    QColor stroke;        // 1 px hairline
    QColor text;          // primary text on glass
    QColor placeholder;   // secondary text on glass
    QColor scrim;         // darkening (dark) or lightening (light) behind bare text
    qreal blurRadius;     // px, MultiEffect blurMax
    static GlassSpec forTheme(const Theme &t);
};

// QML face of the theme for the greeter and the overview: every property follows the theme live.
class Style : public QObject {
    Q_OBJECT
    Q_PROPERTY(QColor ground READ ground NOTIFY changed)
    Q_PROPERTY(QColor text READ text NOTIFY changed)
    Q_PROPERTY(QColor textSecondary READ textSecondary NOTIFY changed)
    Q_PROPERTY(QColor textTertiary READ textTertiary NOTIFY changed)
    Q_PROPERTY(QColor stroke READ stroke NOTIFY changed)
    Q_PROPERTY(QColor accent READ accent NOTIFY changed)
    Q_PROPERTY(QColor danger READ danger NOTIFY changed)
    Q_PROPERTY(QColor warning READ warning NOTIFY changed)
    Q_PROPERTY(QColor glassTint READ glassTint NOTIFY changed)
    Q_PROPERTY(QColor glassVeil READ glassVeil NOTIFY changed)
    Q_PROPERTY(QColor glassStroke READ glassStroke NOTIFY changed)
    Q_PROPERTY(QColor glassHighlight READ glassHighlight NOTIFY changed)
    Q_PROPERTY(QColor scrim READ scrim NOTIFY changed)
    Q_PROPERTY(qreal blurRadius READ blurRadius CONSTANT)
    Q_PROPERTY(bool dark READ dark NOTIFY changed)
    Q_PROPERTY(QColor surface READ surface NOTIFY changed)
    Q_PROPERTY(QColor raised READ raised NOTIFY changed)
    Q_PROPERTY(QColor hairline READ hairline NOTIFY changed)
    Q_PROPERTY(QColor accentText READ accentText NOTIFY changed)
    Q_PROPERTY(QColor ok READ ok NOTIFY changed)
    Q_PROPERTY(QColor series1 READ series1 NOTIFY changed)
    Q_PROPERTY(QColor series2 READ series2 NOTIFY changed)
    Q_PROPERTY(QColor selection READ selection NOTIFY changed)
    Q_PROPERTY(QColor focusRing READ focusRing NOTIFY changed)
    Q_PROPERTY(QColor live READ live NOTIFY changed)
    Q_PROPERTY(QColor track READ track NOTIFY changed)
    Q_PROPERTY(QColor scrollbar READ scrollbar NOTIFY changed)
    Q_PROPERTY(bool reduceMotion READ reduceMotion NOTIFY changed)
    Q_PROPERTY(QString windowButtons READ windowButtons NOTIFY changed)
    // The skin and layout the greeter, overview and launcher may follow (see Theme).
    Q_PROPERTY(qreal radiusScale READ radiusScale NOTIFY changed)
    Q_PROPERTY(int borderWidth READ borderWidth NOTIFY changed)
    Q_PROPERTY(qreal shadowStrength READ shadowStrength NOTIFY changed)
    Q_PROPERTY(QString fontStack READ fontStack NOTIFY changed)
    Q_PROPERTY(QString dockMode READ dockMode NOTIFY changed)
    Q_PROPERTY(QString dockEdge READ dockEdge NOTIFY changed)
    Q_PROPERTY(int taskbarHeight READ taskbarHeight NOTIFY changed)
    Q_PROPERTY(QString launcherPlace READ launcherPlace NOTIFY changed)
    Q_PROPERTY(QString dockSurface READ dockSurface NOTIFY changed)
    Q_PROPERTY(QColor attention READ attention NOTIFY changed)
    Q_PROPERTY(QColor accentDim READ accentDim NOTIFY changed)
    Q_PROPERTY(QString fontDisplayStack READ fontDisplayStack NOTIFY changed)
    Q_PROPERTY(QString fontDataStack READ fontDataStack NOTIFY changed)
    Q_PROPERTY(bool upperTitles READ upperTitles NOTIFY changed)
    Q_PROPERTY(qreal titleTracking READ titleTracking NOTIFY changed)
    Q_PROPERTY(bool scanlines READ scanlines NOTIFY changed)
    Q_PROPERTY(QColor bevelLight READ bevelLight NOTIFY changed)
    Q_PROPERTY(QColor bevelDark READ bevelDark NOTIFY changed)
    // Type and space follow the theme too: the body size (skin.fontSize) and the density (compact, regular, comfortable).
    Q_PROPERTY(int fontSize READ fontSize NOTIFY changed)
    Q_PROPERTY(qreal spacingFactor READ spacingFactor NOTIFY changed)
public:
    explicit Style(const Theme *t, QObject *parent = nullptr) : QObject(parent), m_t(t) {
        connect(t, &Theme::changed, this, &Style::changed);
    }
    QColor ground() const { return m_t->ground(); }
    QColor text() const { return m_t->text(); }
    QColor textSecondary() const { return m_t->textSecondary(); }
    QColor textTertiary() const { return m_t->textTertiary(); }
    QColor stroke() const { return m_t->stroke(); }
    QColor accent() const { return m_t->accent(); }
    QColor danger() const { return m_t->danger(); }
    QColor warning() const { return m_t->warning(); }
    QColor glassTint() const { return m_t->glassTint(); }
    QColor glassVeil() const { return m_t->glassVeil(); }
    QColor glassStroke() const { return m_t->glassStroke(); }
    QColor glassHighlight() const { return m_t->glassHighlight(); }
    QColor scrim() const { return m_t->scrim(); }
    qreal blurRadius() const { return m_t->blurRadius(); }
    bool dark() const { return m_t->dark(); }
    bool reduceMotion() const { return m_t->reduceMotion(); }
    QString windowButtons() const { return m_t->windowButtons(); }
    qreal radiusScale() const { return m_t->radiusScale(); }
    int borderWidth() const { return m_t->borderWidth(); }
    qreal shadowStrength() const { return m_t->shadowStrength(); }
    QString fontStack() const { return m_t->fontStack(); }
    QString dockMode() const { return m_t->dockMode(); }
    QString dockEdge() const { return m_t->dockEdge(); }
    int taskbarHeight() const { return m_t->taskbarHeight(); }
    QString launcherPlace() const { return m_t->launcherPlace(); }
    QString dockSurface() const { return m_t->dockSurface(); }
    QColor attention() const { return m_t->attention(); }
    QColor accentDim() const { return m_t->accentDim(); }
    QString fontDisplayStack() const { return m_t->fontDisplayStack(); }
    QString fontDataStack() const { return m_t->fontDataStack(); }
    bool upperTitles() const { return m_t->upperTitles(); }
    qreal titleTracking() const { return m_t->titleTracking(); }
    bool scanlines() const { return m_t->scanlines(); }
    QColor bevelLight() const { return m_t->bevelLight(); }
    QColor bevelDark() const { return m_t->bevelDark(); }
    int fontSize() const { return m_t->fontSize(); }
    qreal spacingFactor() const { return m_t->spacingFactor(); }
    // A spacing in px on the 4 px grid, scaled by the theme's density.
    Q_INVOKABLE qreal space(qreal base) const { return m_t->space(base); }
    Q_INVOKABLE qreal radius(qreal base) const { return m_t->radius(base); }
    QColor surface() const { return m_t->surface(); }
    QColor raised() const { return m_t->raised(); }
    QColor hairline() const { return m_t->hairline(); }
    QColor accentText() const { return m_t->accentText(); }
    QColor ok() const { return m_t->ok(); }
    QColor series1() const { return m_t->series1(); }
    QColor series2() const { return m_t->series2(); }
    QColor selection() const { return m_t->selection(); }
    QColor focusRing() const { return m_t->focusRing(); }
    QColor live() const { return m_t->live(); }
    QColor track() const { return m_t->track(); }
    QColor scrollbar() const { return m_t->scrollbar(); }
signals:
    void changed();
private:
    const Theme *m_t;
};
