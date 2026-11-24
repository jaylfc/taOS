// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QColor>
#include <QObject>
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
    // Glass, scrim and highlights, all derived from the tokens above.
    Q_PROPERTY(QColor glassTint READ glassTint NOTIFY changed)
    Q_PROPERTY(QColor glassVeil READ glassVeil NOTIFY changed)
    Q_PROPERTY(QColor glassStroke READ glassStroke NOTIFY changed)
    Q_PROPERTY(QColor glassHighlight READ glassHighlight NOTIFY changed)
    Q_PROPERTY(QColor scrim READ scrim NOTIFY changed)
    Q_PROPERTY(qreal blurRadius READ blurRadius CONSTANT)
    Q_PROPERTY(bool reduceMotion READ reduceMotion NOTIFY changed)
    Q_PROPERTY(QString windowButtons READ windowButtons NOTIFY changed)
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

signals:
    void changed();

private:
    void setCurrent(double t);
    QString m_id, m_name, m_variant = QStringLiteral("dark"), m_source;
    TokenSet m_from, m_to, m_cur;
    QString m_themeButtons = QStringLiteral("left"), m_buttonsOverride;
    bool m_have = false, m_reduce = false;
    int m_transitionMs = 0;
    QVariantAnimation m_anim;
};
