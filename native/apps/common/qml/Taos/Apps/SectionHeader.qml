// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A shelf's title with "See all" at its right.
RowLayout {
    id: root
    property string title: ""
    property string actionText: qsTr("See all")
    property bool showAction: true
    signal action()
    spacing: Look.space2
    TaosText {
        text: root.title
        Layout.fillWidth: true
        font.pixelSize: Look.heading
        font.weight: Font.DemiBold
        Accessible.ignored: false
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }
    C.AbstractButton {
        id: link
        visible: root.showAction
        implicitHeight: 28
        implicitWidth: lbl.implicitWidth + Look.space4 * 2
        hoverEnabled: true
        focusPolicy: Qt.StrongFocus
        text: root.actionText
        Accessible.role: Accessible.Link
        Accessible.name: root.actionText + ", " + root.title
        Accessible.onPressAction: clicked()
        Keys.onReturnPressed: clicked()
        onClicked: root.action()
        background: Rectangle {
            radius: height / 2
            color: link.down ? Look.pressed : link.hovered ? Look.hover : "transparent"
            border.width: link.visualFocus ? 2 : 0
            border.color: Look.focusRing
            Behavior on color { ColorAnimation { duration: Look.fast } }
        }
        contentItem: TaosText {
            id: lbl
            text: link.text
            font.pixelSize: Look.body
            font.weight: Font.Medium
            color: Look.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
}
