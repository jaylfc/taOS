pragma Singleton
import QtQuick

// Style tokens for native taOS apps. The names and the dark values mirror the
// `taos-default` theme package (native/shell/common/resources/themes/taos-default/
// theme.json, `Theme` class in native/shell/common/theme.h). When that library
// is merged, replace the literals below with bindings to a Theme instance so
// the apps follow the user's theme; nothing else in an app needs to change.
QtObject {
    readonly property color ground: "#0E0E10"
    readonly property color surface: "#17171A"
    readonly property color raised: "#1F1F23"
    readonly property color stroke: "#1FFFFFFF"
    readonly property color text: "#F2F2F4"
    readonly property color textSecondary: "#B4B4BC"
    readonly property color textTertiary: "#84848E"
    readonly property color accent: "#7C86FF"
    readonly property color warning: "#F5B544"
    readonly property color danger: "#FF6B6B"

    readonly property int spacing: 12
    readonly property int radius: 10
    readonly property int bodySize: 14
    readonly property int titleSize: 18
}
