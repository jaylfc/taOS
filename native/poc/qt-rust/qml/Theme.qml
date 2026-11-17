pragma Singleton
import QtQuick

QtObject {
    property bool dark: true
    readonly property color ground: dark ? "#0E0E10" : "#F6F6F8"
    readonly property color surface: dark ? "#17171A" : "#FFFFFF"
    readonly property color text: dark ? "#F2F2F4" : "#1C1C1E"
}
