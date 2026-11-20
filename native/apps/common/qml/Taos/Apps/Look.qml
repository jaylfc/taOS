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
    readonly property bool dark: TaosStyle.dark
    readonly property color ground: TaosStyle.ground
    readonly property color text: TaosStyle.text
    readonly property color textSecondary: TaosStyle.textSecondary
    readonly property color textTertiary: TaosStyle.textTertiary
    readonly property color accent: TaosStyle.accent
    readonly property color danger: TaosStyle.danger
    readonly property color warning: TaosStyle.warning
    readonly property color success: dark ? "#3DD68C" : "#1E8E54"
    readonly property color hairline: TaosStyle.stroke
    readonly property color hover: Qt.rgba(text.r, text.g, text.b, 0.07)
    readonly property color pressed: Qt.rgba(text.r, text.g, text.b, 0.12)
    readonly property color glassTint: TaosStyle.glassTint
    readonly property color glassVeil: s ? s.glassVeil : (dark ? "#0DFFFFFF" : "#40FFFFFF")
    readonly property color glassStroke: TaosStyle.glassStroke
    // A card that sits on the window: translucent surface so the backdrop shows through.
    readonly property color card: Qt.rgba(TaosStyle.surface.r, TaosStyle.surface.g, TaosStyle.surface.b, dark ? 0.78 : 0.86)
    readonly property color cardUnread: Qt.tint(card, Qt.rgba(accent.r, accent.g, accent.b, 0.10))
    readonly property color field: Qt.rgba(text.r, text.g, text.b, 0.06)
    readonly property color onAccent: dark ? "#0B0B0D" : "#FFFFFF"

    function tone(name) {
        switch (name) {
        case "success": return success
        case "warning": return warning
        case "error": case "danger": return danger
        case "info": case "accent": return accent
        default: return textSecondary
        }
    }
    function tint(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

    // type scale (px)
    readonly property int caption: 11
    readonly property int small: 12
    readonly property int body: 13
    readonly property int bodyLarge: 14
    readonly property int title: 15
    readonly property int heading: 20

    readonly property int gap: 8
    readonly property int pad: 16
    readonly property int radiusCard: 14
    readonly property int radiusControl: 8

    // motion: zero when the user asked for reduced motion
    readonly property bool reduceMotion: TaosStyle.reduceMotion
    readonly property int fast: reduceMotion ? 0 : 120
    readonly property int base: reduceMotion ? 0 : 200
    readonly property int slow: reduceMotion ? 0 : 320
}
