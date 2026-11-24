pragma Singleton
import QtQuick
import Taos.Style 1.0

// App-local design tokens on top of the shared Taos.Style: semantic status
// colours, glass fills, type scale and the motion helper. Nothing here is
// data; colours that the shared style already has are taken from it.
QtObject {
    readonly property string family: "Inter"
    readonly property bool dark: TaosStyle.dark

    // Every colour is a theme token (Taos.Style); nothing here is hardcoded.
    // `info` and `violet` are gone: categories are told apart by label, not hue.
    readonly property color ok: TaosStyle.ok
    readonly property color warn: TaosStyle.warning
    readonly property color crit: TaosStyle.danger
    readonly property color accent: TaosStyle.accent
    readonly property color live: TaosStyle.live
    readonly property color focusRing: TaosStyle.focusRing
    readonly property color series1: TaosStyle.series1
    readonly property color series2: TaosStyle.series2
    readonly property color neutral: TaosStyle.textSecondary

    // Surfaces: an opaque surface with one hairline (no shadow, no sheen).
    readonly property color glassFill: TaosStyle.surface
    readonly property color glassFillHover: TaosStyle.raised
    readonly property color inner: Qt.rgba(TaosStyle.text.r, TaosStyle.text.g, TaosStyle.text.b, 0.04)
    readonly property color hairline: TaosStyle.hairline
    readonly property color track: TaosStyle.track

    readonly property int radius: 14
    readonly property int innerRadius: 10
    readonly property int pad: 16
    readonly property int gap: 12

    function load(p) { return p < 80 ? series1 : (p < 95 ? warn : crit) }
    function dur(ms) { return TaosStyle.reduceMotion ? 0 : ms }
    function tone(t) { return t === 1 ? ok : t === 2 ? warn : t === 3 ? crit : TaosStyle.textSecondary }
    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
}
