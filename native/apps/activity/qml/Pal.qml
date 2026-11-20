pragma Singleton
import QtQuick
import Taos.Style 1.0

// App-local design tokens on top of the shared Taos.Style: semantic status
// colours, glass fills, type scale and the motion helper. Nothing here is
// data; colours that the shared style already has are taken from it.
QtObject {
    readonly property string family: "Inter"
    readonly property bool dark: TaosStyle.dark

    readonly property color ok: dark ? "#34D399" : "#047857"
    readonly property color warn: TaosStyle.warning
    readonly property color crit: TaosStyle.danger
    readonly property color info: dark ? "#5CC8F5" : "#0B6FA4"
    readonly property color accent: TaosStyle.accent
    readonly property color violet: dark ? "#B58CFF" : "#6D3FD1"

    // Glass: the shell's tint over the window ground. Blur needs a compositor
    // that provides it; without one the tint and the hairline carry the look.
    readonly property color glassFill: dark ? Qt.rgba(1, 1, 1, 0.055) : Qt.rgba(1, 1, 1, 0.66)
    readonly property color glassFillHover: dark ? Qt.rgba(1, 1, 1, 0.09) : Qt.rgba(1, 1, 1, 0.8)
    readonly property color inner: dark ? Qt.rgba(1, 1, 1, 0.04) : Qt.rgba(0, 0, 0, 0.035)
    readonly property color hairline: dark ? Qt.rgba(1, 1, 1, 0.10) : Qt.rgba(0, 0, 0, 0.10)
    readonly property color sheen: dark ? Qt.rgba(1, 1, 1, 0.07) : Qt.rgba(1, 1, 1, 0.9)
    readonly property color track: dark ? Qt.rgba(1, 1, 1, 0.08) : Qt.rgba(0, 0, 0, 0.08)

    readonly property int radius: 16
    readonly property int innerRadius: 10
    readonly property int pad: 16
    readonly property int gap: 12

    function load(p) { return p < 50 ? ok : (p < 80 ? warn : crit) }
    function dur(ms) { return TaosStyle.reduceMotion ? 0 : ms }
    function tone(t) { return t === 1 ? ok : t === 2 ? warn : t === 3 ? crit : t === 4 ? info : TaosStyle.textSecondary }
    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
}
