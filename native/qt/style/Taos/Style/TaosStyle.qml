pragma Singleton
import QtQuick
import Taos 1.0

// Style tokens for native taOS apps. The colours come from the shell's own
// `Style` (native/shell/common: Theme + Style, the person's theme from
// ~/.config/taos/appearance.json), exposed as `Taos.style`. Every property
// follows a live theme or mode switch. The literals are only the fallback
// (graphite, dark) when no theme could be loaded at all.
QtObject {
    readonly property QtObject s: Taos.style
    readonly property color ground: s ? s.ground : "#0B0B0C"
    readonly property color text: s ? s.text : "#EDEDEE"
    readonly property color textSecondary: s ? s.textSecondary : "#A3A3A8"
    readonly property color textTertiary: s ? s.textTertiary : "#85858A"
    readonly property color accent: s ? s.accent : "#F4F4F5"
    readonly property color warning: s ? s.warning : "#D1A257"
    readonly property color danger: s ? s.danger : "#E0786F"
    readonly property color stroke: s ? s.stroke : "#14FFFFFF"
    // Explicit surfaces and the optional tokens of a theme package; a theme that
    // lacks them gets them derived from ground and text (as before).
    readonly property color surface: s ? s.surface : Qt.tint(ground, Qt.rgba(text.r, text.g, text.b, 0.05))
    readonly property color raised: s ? s.raised : Qt.tint(ground, Qt.rgba(text.r, text.g, text.b, 0.09))
    readonly property color hairline: s ? s.hairline : stroke
    readonly property color accentText: s ? s.accentText : (dark ? "#0B0B0C" : "#FFFFFF")
    readonly property color ok: s ? s.ok : (dark ? "#6FBF8E" : "#1E8E54")
    readonly property color series1: s ? s.series1 : accent
    readonly property color series2: s ? s.series2 : Qt.rgba(text.r, text.g, text.b, 0.5)
    readonly property color selection: s ? s.selection : Qt.rgba(accent.r, accent.g, accent.b, 0.16)
    readonly property color focusRing: s ? s.focusRing : accent
    readonly property color live: s ? s.live : accent
    readonly property color track: s ? s.track : Qt.rgba(text.r, text.g, text.b, 0.08)
    readonly property color scrollbar: s ? s.scrollbar : Qt.rgba(text.r, text.g, text.b, 0.25)
    // Frosted glass, as the shell paints it (blur needs a compositor that provides it).
    readonly property color glassTint: s ? s.glassTint : "#850B0B0C"
    readonly property color glassStroke: s ? s.glassStroke : "#3DEDEDEE"
    readonly property color glassVeil: s ? s.glassVeil : "#1CEDEDEE"
    readonly property color glassHighlight: s ? s.glassHighlight : "#38EDEDEE"
    readonly property color scrim: s ? s.scrim : "#590B0B0C"
    readonly property real blurRadius: s ? s.blurRadius : 64
    readonly property bool dark: s ? s.dark : true
    readonly property bool reduceMotion: s ? s.reduceMotion : false

    // Which side the window buttons are on: "left" (default) or "right", from the theme and the person's choice.
    readonly property string windowButtons: s && s.windowButtons !== undefined ? s.windowButtons : "left"

    readonly property int spacing: 12
    readonly property int radius: 14
    readonly property int bodySize: 14
    readonly property int titleSize: 18
}
