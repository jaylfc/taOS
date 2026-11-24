import QtQuick
import Taos.Launcher

// Tile for results whose icon the theme cannot draw: a quiet disc with the initials.
Item {
    id: mg
    property real diameter: 32
    property string name: ""
    readonly property string initials: {
        const parts = name.replace(/[^A-Za-z0-9 ._-]/g, "").split(/[ ._-]+/).filter(s => s.length > 0);
        if (parts.length === 0) return "?";
        if (parts.length === 1) return parts[0].charAt(0).toUpperCase() + parts[0].substring(1, 2).toLowerCase();
        return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
    }
    width: diameter; height: diameter
    Accessible.ignored: true
    Rectangle {
        anchors.fill: parent
        radius: width * 0.3
        color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.12 : 0.09)
        border.width: 1
        border.color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.14 : 0.12)
    }
    Text {
        anchors.centerIn: parent
        text: mg.initials
        color: Style.textSecondary
        font.family: "Inter"
        font.weight: Font.Medium
        font.pixelSize: mg.diameter * 0.38
    }
}
