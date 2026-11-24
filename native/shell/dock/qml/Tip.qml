import QtQuick
import Taos.Dock

// A dock tooltip: its own small surface, sized to the text.
Rectangle {
    id: tip
    property string text: ""
    implicitWidth: label.implicitWidth + 18
    implicitHeight: label.implicitHeight + 10
    radius: 7
    color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, 0.94)
    border.width: 1
    border.color: Theme.glassStroke
    Accessible.role: Accessible.ToolTip
    Accessible.name: text
    Text {
        id: label
        anchors.centerIn: parent
        text: tip.text
        color: Theme.text
        font.family: "Inter, Noto Sans, DejaVu Sans, sans-serif"
        font.pixelSize: 12
    }
}
