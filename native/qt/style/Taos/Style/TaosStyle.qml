pragma Singleton
import QtQuick
import Taos 1.0

// Style tokens for native taOS apps. The colours come from the shell's own
// `Style` (native/shell/common: Theme + Style, the taos-default theme package
// and the user's theme), exposed as `Taos.style`. The literals are only the
// fallback when no theme could be loaded. surface and raised are derived from
// the theme's ground and text so they follow dark and light.
QtObject {
    readonly property QtObject s: Taos.style
    readonly property color ground: s ? s.ground : "#0E0E10"
    readonly property color text: s ? s.text : "#F2F2F4"
    readonly property color textSecondary: s ? s.textSecondary : "#B4B4BC"
    readonly property color textTertiary: s ? s.textTertiary : "#84848E"
    readonly property color accent: s ? s.accent : "#7C86FF"
    readonly property color warning: s ? s.warning : "#F5B544"
    readonly property color danger: s ? s.danger : "#FF6B6B"
    readonly property color stroke: s ? s.stroke : "#1FFFFFFF"
    readonly property color surface: Qt.tint(ground, Qt.rgba(text.r, text.g, text.b, 0.05))
    readonly property color raised: Qt.tint(ground, Qt.rgba(text.r, text.g, text.b, 0.09))
    // Frosted glass, as the shell paints it (blur needs a compositor that provides it).
    readonly property color glassTint: s ? s.glassTint : "#850E0E10"
    readonly property color glassStroke: s ? s.glassStroke : "#3DF2F2F4"
    readonly property real blurRadius: s ? s.blurRadius : 64
    readonly property bool dark: s ? s.dark : true
    readonly property bool reduceMotion: s ? s.reduceMotion : false

    readonly property int spacing: 12
    readonly property int radius: 10
    readonly property int bodySize: 14
    readonly property int titleSize: 18
}
