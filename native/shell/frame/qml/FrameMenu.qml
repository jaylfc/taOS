// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as B
import Taos.Frame

// The window menu (right click on a title bar): minimise, maximise, full screen, close. The names are the taos-frame
// actions: "full" is the maximise toggle (the middle button), "fullscreen" is sway's own fullscreen. A full-output transparent surface so a
// click outside closes it; Escape too.
Item {
    id: root
    property real anchorX: 0
    property real anchorY: 0
    property string windowTitle: ""
    property var bridge: null             // C++: choose(button), dismiss()
    signal chosen(string button)
    signal dismissed()
    onChosen: (button) => { if (bridge) bridge.choose(button) }
    onDismissed: { if (bridge) bridge.dismiss() }
    focus: true
    Keys.onEscapePressed: root.dismissed()
    Accessible.role: Accessible.PopupMenu
    Accessible.name: qsTr("Window menu for %1").arg(windowTitle)

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onPressed: root.dismissed() }

    Rectangle {
        id: panel
        x: Math.max(4, Math.min(root.width - width - 4, root.anchorX))
        y: Math.max(4, Math.min(root.height - height - 4, root.anchorY))
        width: 168
        height: col.implicitHeight + 8
        radius: 8
        color: Theme.raised
        border.width: 1
        border.color: Theme.hairline
        Column {
            id: col
            x: 4
            y: 4
            width: parent.width - 8
            Repeater {
                model: [{name: "minimise", text: qsTr("Minimise")}, {name: "full", text: qsTr("Maximise")}, {name: "fullscreen", text: qsTr("Full screen")}, {name: "close", text: qsTr("Close")}]
                Rectangle {
                    id: row
                    required property var modelData
                    required property int index
                    width: col.width
                    height: 28
                    radius: 5
                    color: hov.hovered || activeFocus ? Theme.selection : "transparent"
                    activeFocusOnTab: true
                    Accessible.role: Accessible.MenuItem
                    Accessible.name: modelData.text
                    Accessible.onPressAction: root.chosen(modelData.name)
                    Component.onCompleted: if (index === 0) forceActiveFocus()
                    Keys.onReturnPressed: root.chosen(modelData.name)
                    Keys.onEnterPressed: root.chosen(modelData.name)
                    Keys.onDownPressed: nextItemInFocusChain(true).forceActiveFocus()
                    Keys.onUpPressed: nextItemInFocusChain(false).forceActiveFocus()
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        leftPadding: 8
                        text: row.modelData.text
                        color: row.modelData.name === "close" && (hov.hovered || row.activeFocus) ? Theme.danger : Theme.text
                        font.pixelSize: 14
                    }
                    HoverHandler { id: hov }
                    TapHandler { onTapped: root.chosen(row.modelData.name) }
                }
            }
        }
    }
}
