// SPDX-License-Identifier: AGPL-3.0-or-later
pragma Singleton
import QtQuick
import Taos 1.0
import Taos.Style 1.0

// The look of the first-party native apps: Taos.Style's tokens (the shell's own
// theme) plus the few derived values the apps need, one type scale and the
// motion numbers. Nothing here is a second theme: it follows TaosStyle, so a
// theme change and dark/light reach every app together.
QtObject {
    readonly property QtObject s: Taos.style

    // Inter is the taOS face; the generic family is the fallback when it is not installed.
    readonly property string family: "Inter"
    readonly property string monoFamily: "monospace"   // ids, ports and firmware versions
    readonly property bool dark: TaosStyle.dark
    readonly property color ground: TaosStyle.ground
    readonly property color text: TaosStyle.text
    readonly property color textSecondary: TaosStyle.textSecondary
    readonly property color textTertiary: TaosStyle.textTertiary
    readonly property color accent: TaosStyle.accent
    readonly property color danger: TaosStyle.danger
    readonly property color warning: TaosStyle.warning
    readonly property color success: TaosStyle.ok
    readonly property color live: TaosStyle.live
    readonly property color selection: TaosStyle.selection      // selected row / chip fill
    readonly property color focusRing: TaosStyle.focusRing
    readonly property color scrollbar: TaosStyle.scrollbar
    readonly property color surface: TaosStyle.surface
    readonly property color raised: TaosStyle.raised
    readonly property color series1: TaosStyle.series1
    readonly property color series2: TaosStyle.series2
    readonly property color hairline: TaosStyle.hairline
    readonly property color hover: Qt.rgba(text.r, text.g, text.b, 0.07)
    readonly property color pressed: Qt.rgba(text.r, text.g, text.b, 0.12)
    readonly property color glassTint: TaosStyle.glassTint
    readonly property color glassVeil: s ? s.glassVeil : (dark ? "#0DFFFFFF" : "#40FFFFFF")
    readonly property color glassStroke: TaosStyle.glassStroke
    // A card that sits on the window: translucent surface so the backdrop shows through.
    readonly property color card: TaosStyle.surface
    readonly property color cardUnread: TaosStyle.raised
    readonly property color field: Qt.rgba(text.r, text.g, text.b, 0.06)
    readonly property color accentText: TaosStyle.accentText
    readonly property color scrim: TaosStyle.scrim

    function tone(name) {
        switch (name) {
        case "success": return success
        case "warning": return warning
        case "error": case "danger": return danger
        case "accent": return accent
        default: return textSecondary
        }
    }
    function tint(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
    // A veil of the window ground, for text that sits on artwork: it follows the theme, so a light theme gets a light veil.
    function veil(a) { return Qt.rgba(ground.r, ground.g, ground.b, a) }
    function hashOf(s) { let h = 7; const t = String(s); for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) & 0x7fffffff; return h }
    // The fill of a stand-in tile or cover for a name: the text colour at a low strength, one of six, so the
    // fallbacks differ a little from each other and never leave the theme's neutral range.
    function tileFill(name) { return tint(text, 0.07 + (hashOf(name) % 6) * 0.02) }

    // type scale (px)
    readonly property int caption: 11
    readonly property int small: 12
    readonly property int body: 13
    readonly property int bodyMedium: 14
    readonly property int bodyLarge: 15
    readonly property int title: 15
    readonly property int heading: 20
    readonly property int pageTitle: 24      // a page's title
    readonly property int cardTitle: 16      // the name on a card
    readonly property int display: 34        // the name on a hero

    readonly property int space1: 4          // the 4 px grid: 8 is the unit
    readonly property int space2: 8
    readonly property int space3: 12
    readonly property int space4: 16
    readonly property int space5: 24
    readonly property int space6: 32
    readonly property int gap: 8
    readonly property int pad: 16
    readonly property int radiusCard: 14
    readonly property int radiusControl: 8

    // motion: zero when the user asked for reduced motion
    readonly property bool reduceMotion: TaosStyle.reduceMotion
    readonly property int fast: reduceMotion ? 0 : 120
    readonly property int base: reduceMotion ? 0 : 200
    readonly property int slow: reduceMotion ? 0 : 320
    readonly property int ease: Easing.OutCubic
}
