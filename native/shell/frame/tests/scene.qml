import QtQuick
import Taos.Frame
import "../qml"

// Dev scene for frameshot: a stand-in for a framed window as sway paints it (title bar in the theme's colours with the
// title centred, hairlines top and bottom) with the strip over its button corner.
Item {
    id: scene
    property string title: "YouTube"
    property bool buttonsRight: false
    property var buttons: []
    property bool active: true
    readonly property color barBg: Theme.surface
    readonly property color hair: Qt.tint(Theme.surface, Theme.hairline)
    Rectangle { anchors.fill: parent; color: Theme.ground }
    Rectangle {
        id: bar
        x: 0; y: 0; width: parent.width; height: 32
        color: scene.barBg
        Rectangle { width: parent.width; height: 1; color: scene.hair }
        Rectangle { y: parent.height - 1; width: parent.width; height: 1; color: scene.hair }
        Text {
            anchors.centerIn: parent
            text: scene.title
            color: scene.active ? Theme.text : Theme.textSecondary
            font.pixelSize: 14
            font.weight: Font.Medium
        }
    }
    Text { x: 24; y: 56; text: qsTr("Window content"); color: Theme.textSecondary; font.pixelSize: 14 }
    Strip {
        id: strip
        objectName: "strip"
        x: scene.buttonsRight ? bar.width - 8 - width : 8
        y: 4
        width: 80
        height: 24
        buttons: scene.buttons
        active: scene.active
    }
}
