// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// The secondary actions of the selected result (Tab or Right arrow), in place of the result list.
Item {
    id: av
    readonly property real rowH: 42
    implicitHeight: head.height + list.count * rowH + 10
    Accessible.role: Accessible.List
    Accessible.name: qsTr("Actions for %1").arg(Launcher.currentTitle)

    Item {
        id: head
        width: parent.width
        height: 44
        Text {
            x: 22
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 44
            text: qsTr("Actions for %1").arg(Launcher.currentTitle)
            color: Style.textTertiary
            elide: Text.ElideRight
            font.family: "Inter"
            font.pixelSize: 11
            font.weight: Font.DemiBold
            font.letterSpacing: 0.6
            font.capitalization: Font.AllUppercase
            Accessible.ignored: true
        }
    }
    Rectangle {
        id: sel
        x: 8
        width: av.width - 16
        height: av.rowH - 4
        radius: 11
        y: head.height + Launcher.actionIndex * av.rowH + 2
        color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.13 : 0.10)
        Behavior on y { NumberAnimation { duration: Style.reduceMotion ? 0 : 130; easing.type: Easing.OutCubic } }
    }
    Repeater {
        id: list
        model: Launcher.actionLabels
        delegate: Item {
            id: ai
            required property int index
            required property string modelData
            y: head.height + index * av.rowH
            width: av.width
            height: av.rowH
            Accessible.role: Accessible.ListItem
            Accessible.name: qsTr("%1, action %2 of %3").arg(modelData).arg(index + 1).arg(list.count)
            Accessible.selected: index === Launcher.actionIndex
            Text {
                x: 24
                anchors.verticalCenter: parent.verticalCenter
                text: ai.modelData
                color: Style.text
                font.family: "Inter"
                font.pixelSize: 15
                font.weight: Font.Medium
                Accessible.ignored: true
            }
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onPositionChanged: Launcher.setActionIndex(ai.index)
                onClicked: Launcher.runActionAt(ai.index)
                Accessible.ignored: true
            }
        }
    }
}
