// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QColor>
#include <QObject>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVariantAnimation>

#include "themedef.h"

// Theme: the colours and motion preference every shell component paints with.
// All colour properties NOTIFY `changed`, so a QML binding follows a theme or
// variant switch without anything restarting. A switch cross-fades over
// `transitionMs` (150 ms; none under reduced motion): `changed` is emitted on
// every frame of the fade, with the in-between colours.
class Theme : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString id READ id NOTIFY changed)
    Q_PROPERTY(QString name READ name NOTIFY changed)
    Q_PROPERTY(QString variant READ variant NOTIFY changed)
    Q_PROPERTY(bool dark READ dark NOTIFY changed)
    Q_PROPERTY(QString source READ source NOTIFY changed)
    Q_PROPERTY(QColor ground READ ground NOTIFY changed)
    Q_PROPERTY(QColor barBackground READ barBackground NOTIFY changed)
    Q_PROPERTY(QColor stroke READ stroke NOTIFY changed)
    Q_PROPERTY(QColor text READ text NOTIFY changed)
    Q_PROPERTY(QColor textSecondary READ textSecondary NOTIFY changed)
    Q_PROPERTY(QColor textTertiary READ textTertiary NOTIFY changed)
    Q_PROPERTY(QColor accent READ accent NOTIFY changed)
    Q_PROPERTY(QColor warning READ warning NOTIFY changed)
    Q_PROPERTY(QColor danger READ danger NOTIFY changed)
    Q_PROPERTY(QColor hover READ hover NOTIFY changed)
    Q_PROPERTY(QColor pressed READ pressed NOTIFY changed)
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
    Q_PROPERTY(QColor attention READ attention NOTIFY changed)
    Q_PROPERTY(QColor accentDim READ accentDim NOTIFY changed)
    // Glass, scrim and highlights, all derived from the tokens above.
    Q_PROPERTY(QColor glassTint READ glassTint NOTIFY changed)
    Q_PROPERTY(QColor glassVeil READ glassVeil NOTIFY changed)
    Q_PROPERTY(QColor glassStroke READ glassStroke NOTIFY changed)
    Q_PROPERTY(QColor glassHighlight READ glassHighlight NOTIFY changed)
    Q_PROPERTY(QColor scrim READ scrim NOTIFY changed)
    Q_PROPERTY(qreal blurRadius READ blurRadius CONSTANT)
    Q_PROPERTY(bool reduceMotion READ reduceMotion NOTIFY changed)
    Q_PROPERTY(QString windowButtons READ windowButtons NOTIFY changed)
    // Skin (see THEME-FORMAT.md): notify skinChanged, once per theme switch (not on every fade frame).
    Q_PROPERTY(qreal radiusScale READ radiusScale NOTIFY skinChanged)
    Q_PROPERTY(int borderWidth READ borderWidth NOTIFY skinChanged)
    Q_PROPERTY(qreal shadowStrength READ shadowStrength NOTIFY skinChanged)
    Q_PROPERTY(QString fontStack READ fontStack NOTIFY skinChanged)
    Q_PROPERTY(int fontSize READ fontSize NOTIFY skinChanged)
    Q_PROPERTY(qreal iconStroke READ iconStroke NOTIFY skinChanged)
    Q_PROPERTY(int titleBarHeight READ titleBarHeight NOTIFY skinChanged)
    Q_PROPERTY(QString buttonShape READ buttonShape NOTIFY skinChanged)
    Q_PROPERTY(int buttonSize READ buttonSize NOTIFY changed)
    Q_PROPERTY(QStringList buttonOrder READ buttonOrder NOTIFY changed)
    Q_PROPERTY(QString barSurface READ barSurface NOTIFY skinChanged)
    Q_PROPERTY(QString dockSurface READ dockSurface NOTIFY skinChanged)
    Q_PROPERTY(qreal spacingFactor READ spacingFactor NOTIFY skinChanged)
    Q_PROPERTY(int windowRadius READ windowRadius NOTIFY skinChanged)
    Q_PROPERTY(int windowGap READ windowGap NOTIFY skinChanged)
    Q_PROPERTY(QString fontDisplayStack READ fontDisplayStack NOTIFY skinChanged)
    Q_PROPERTY(QString fontDataStack READ fontDataStack NOTIFY skinChanged)
    Q_PROPERTY(bool upperTitles READ upperTitles NOTIFY skinChanged)
    Q_PROPERTY(qreal titleTracking READ titleTracking NOTIFY skinChanged)
    Q_PROPERTY(QString focusBorder READ focusBorder NOTIFY skinChanged)
    Q_PROPERTY(bool brackets READ brackets NOTIFY skinChanged)
    Q_PROPERTY(bool scanlines READ scanlines NOTIFY skinChanged)
    Q_PROPERTY(QString barStyle READ barStyle NOTIFY layoutChanged)
    Q_PROPERTY(QString cellShape READ cellShape NOTIFY layoutChanged)
    Q_PROPERTY(QString activeTitle READ activeTitle NOTIFY skinChanged)
    Q_PROPERTY(int trayWidth READ trayWidth NOTIFY changed)
    Q_PROPERTY(QString wallpaperPath READ wallpaperPath NOTIFY skinChanged)
    Q_PROPERTY(QColor bevelLight READ bevelLight NOTIFY changed)
    Q_PROPERTY(QColor bevelDark READ bevelDark NOTIFY changed)
    // Layout: notify layoutChanged, once per theme switch.
    Q_PROPERTY(bool topBar READ topBar NOTIFY layoutChanged)
    Q_PROPERTY(int barHeight READ barHeight NOTIFY layoutChanged)
    Q_PROPERTY(QString barEdge READ barEdge NOTIFY layoutChanged)
    Q_PROPERTY(QString clockPlace READ clockPlace NOTIFY layoutChanged)
    Q_PROPERTY(QString dockMode READ dockMode NOTIFY layoutChanged)
    Q_PROPERTY(int taskbarHeight READ taskbarHeight NOTIFY layoutChanged)
    Q_PROPERTY(QString dockEdge READ dockEdge NOTIFY layoutChanged)
    Q_PROPERTY(QString launcherPlace READ launcherPlace NOTIFY layoutChanged)
    Q_PROPERTY(QString startLabel READ startLabel NOTIFY layoutChanged)
public:
    static constexpr double kBarOpacity = 0.72;

    explicit Theme(QObject *parent = nullptr);

    // Show `def` in `variant` (falls back to dark when the theme lacks it). Animated when a
    // theme is already showing, transitionMs > 0 and motion is not reduced. Returns false
    // (nothing changes) when the definition has no usable variant.
    bool apply(const ThemeDef &def, const QString &variant, bool reduceMotionFlag);

    // Convenience for tools and tests: the first valid `<dir>/<id>/theme.json` of `searchDirs`,
    // else the theme file at `builtinPath`. Never animates. False (and `error`) when neither is usable.
    bool load(const QString &id, const QString &variant, const QStringList &searchDirs,
              const QString &builtinPath, bool reduceMotionFlag, QString *error = nullptr);

    void setTransitionMs(int ms) { m_transitionMs = ms; }
    int transitionMs() const { return m_transitionMs; }
    bool transitioning() const { return m_anim.state() == QAbstractAnimation::Running; }
    // The colours the current theme settles on (equal to the live ones when no fade runs).
    const TokenSet &target() const { return m_to; }

    QString id() const { return m_id; }
    QString name() const { return m_name; }
    QString variant() const { return m_variant; }
    bool dark() const { return m_variant != QLatin1String("light"); }
    QString source() const { return m_source; }
    QColor ground() const { return m_cur[ThemeTokens::Ground]; }
    QColor barBackground() const;
    QColor stroke() const { return m_cur[ThemeTokens::Stroke]; }
    QColor text() const { return m_cur[ThemeTokens::Text]; }
    QColor textSecondary() const { return m_cur[ThemeTokens::TextSecondary]; }
    QColor textTertiary() const { return m_cur[ThemeTokens::TextTertiary]; }
    QColor accent() const { return m_cur[ThemeTokens::Accent]; }
    QColor warning() const { return m_cur[ThemeTokens::Warning]; }
    QColor danger() const { return m_cur[ThemeTokens::Danger]; }
    QColor hover() const;
    QColor pressed() const;
    QColor surface() const { return m_cur[ThemeTokens::Surface]; }
    QColor raised() const { return m_cur[ThemeTokens::Raised]; }
    QColor hairline() const { return m_cur[ThemeTokens::Hairline]; }
    QColor accentText() const { return m_cur[ThemeTokens::AccentText]; }
    QColor ok() const { return m_cur[ThemeTokens::Ok]; }
    QColor series1() const { return m_cur[ThemeTokens::Series1]; }
    QColor series2() const { return m_cur[ThemeTokens::Series2]; }
    QColor selection() const { return m_cur[ThemeTokens::Selection]; }
    QColor focusRing() const { return m_cur[ThemeTokens::FocusRing]; }
    QColor live() const { return m_cur[ThemeTokens::Live]; }
    QColor track() const { return m_cur[ThemeTokens::Track]; }
    QColor scrollbar() const { return m_cur[ThemeTokens::Scrollbar]; }
    QColor attention() const { return m_cur[ThemeTokens::Attention]; }
    QColor accentDim() const { return m_cur[ThemeTokens::AccentDim]; }
    QColor glassTint() const;
    QColor glassVeil() const;
    QColor glassStroke() const;
    QColor glassHighlight() const;
    QColor scrim() const;
    qreal blurRadius() const { return 64; }
    bool reduceMotion() const { return m_reduce; }
    // Which side the window buttons are on: the person's choice if they made one, else the theme's. "left" or "right".
    QString windowButtons() const { return m_buttonsOverride.isEmpty() ? m_themeButtons : m_buttonsOverride; }
    // "" follows the theme. Emits `changed` when the effective side changes.
    void setWindowButtonsOverride(const QString &side);

    const Skin &skin() const { return m_skin; }
    const LayoutSpec &layout() const { return m_layout; }
    qreal radiusScale() const { return m_skin.radiusScale; }
    int borderWidth() const { return m_skin.borderWidth; }
    qreal shadowStrength() const { return m_skin.shadowStrength; }
    // The font family list for QML `font.family`: the theme's family first (when it gives one), then the shipped stack.
    QString fontStack() const;
    int fontSize() const { return m_skin.fontSize; }
    qreal iconStroke() const { return m_skin.iconStroke; }
    int titleBarHeight() const { return m_skin.titleBarHeight; }
    QString buttonShape() const { return m_skin.buttonShape; }
    int buttonSize() const { return m_skin.buttonSize; }
    // close, minimise, full in on-screen order, left to right: the theme's order, else by the side in effect.
    QStringList buttonOrder() const { return m_skin.buttonOrder.isEmpty() ? defaultButtonOrder(windowButtons()) : m_skin.buttonOrder; }
    QString barSurface() const { return m_skin.barSurface; }
    QString dockSurface() const { return m_skin.dockSurface; }
    qreal spacingFactor() const;
    int windowRadius() const { return m_skin.windowRadius; }
    int windowGap() const { return m_skin.windowGap; }
    QString fontDisplayStack() const;   // the display face (titles, labels) then the body stack
    QString fontDataStack() const;      // the data face (numbers, readouts) then the body stack
    bool upperTitles() const { return m_skin.titleCase == QLatin1String("upper"); }
    qreal titleTracking() const { return m_skin.titleTracking; }
    QString focusBorder() const { return m_skin.focusBorder; }
    bool brackets() const { return m_skin.brackets; }
    bool scanlines() const { return m_skin.texture == QLatin1String("scanlines"); }
    QString barStyle() const { return m_layout.barStyle; }
    QString cellShape() const { return m_layout.cellShape; }
    QString activeTitle() const { return m_skin.activeTitle; }
    // The width of the taskbar's tray (display, network, sound and the clock), derived from the skin so that taos-bar,
    // which draws the tray, and taos-dock, which draws the taskbar beside it, agree without talking to each other.
    int trayWidth() const;
    QString wallpaperPath() const { return m_wallpaper; }   // absolute, "" when the theme has none
    QColor bevelLight() const;   // the lit edge of a raised surface, derived from the tokens
    QColor bevelDark() const;    // the shaded edge
    // A corner radius the way the skin wants it: `base` (what the shell drew before skins) times radiusScale.
    Q_INVOKABLE qreal radius(qreal base) const { return qRound(base * m_skin.radiusScale); }
    // A spacing in px scaled by the density.
    Q_INVOKABLE qreal space(qreal base) const { return qRound(base * spacingFactor()); }

    bool topBar() const { return m_layout.topBar; }
    int barHeight() const { return m_layout.barHeight; }
    QString barEdge() const { return m_layout.barEdge; }
    QString clockPlace() const { return m_layout.clock; }
    QString dockMode() const { return m_layout.dock; }
    int taskbarHeight() const { return m_layout.taskbarHeight; }
    QString dockEdge() const { return m_layout.dockEdge; }
    QString launcherPlace() const { return m_layout.launcher; }
    QString startLabel() const { return m_layout.startLabel; }

signals:
    void changed();
    void skinChanged();     // once per switch that changes the skin
    void layoutChanged();   // once per switch that changes the layout

private:
    void setCurrent(double t);
    QString m_id, m_name, m_variant = QStringLiteral("dark"), m_source;
    TokenSet m_from, m_to, m_cur;
    Skin m_skin;
    LayoutSpec m_layout;
    QString m_wallpaper;
    QSet<QString> m_fontFiles;
    QString m_themeButtons = QStringLiteral("left"), m_buttonsOverride;
    bool m_have = false, m_reduce = false;
    int m_transitionMs = 0;
    QVariantAnimation m_anim;
};
