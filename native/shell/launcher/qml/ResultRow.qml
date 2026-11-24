// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// One result: icon, title, subtitle and, on the selected row, the hint of what Enter does.
// The section label sits above the first row of a group; the row itself is `body`.
Item {
    id: row
    required property int index
    required property string section
    required property bool firstInSection
    required property string title
    required property string subtitle
    required property string icon
    required property bool hasIcon
    required property string kind
    required property string hint
    required property bool topHit
    required property string accessibleText
    property bool ctrlHeld: false

    readonly property bool selected: index === Launcher.currentIndex
    readonly property real headerH: firstInSection ? 30 : 0
    readonly property real bodyH: topHit ? 68 : 52
    readonly property real iconSize: topHit ? 44 : 32
    readonly property string hintText: selected ? Launcher.currentHint : ""
    readonly property real bodyY: y + headerH   // in the list's content coordinates

    width: ListView.view ? ListView.view.width : 0
    height: headerH + bodyH

    Accessible.role: Accessible.ListItem
    Accessible.name: accessibleText
    Accessible.selected: selected
    Accessible.focusable: true

    Text {
        visible: row.firstInSection
        x: 22
        y: 10
        text: row.section
        color: Style.textTertiary
        font.family: "Inter"
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 0.6
        font.capitalization: Font.AllUppercase
        Accessible.ignored: true
    }

    Item {
        id: body
        y: row.headerH
        width: parent.width
        height: row.bodyH

        Item {
            id: iconBox
            x: 18
            width: row.iconSize
            height: row.iconSize
            anchors.verticalCenter: parent.verticalCenter
            transformOrigin: Item.Center
            Image {
                anchors.fill: parent
                visible: row.hasIcon
                source: row.hasIcon ? row.icon : ""
                sourceSize: Qt.size(row.iconSize * 2, row.iconSize * 2)
                fillMode: Image.PreserveAspectFit
                asynchronous: false
                smooth: true
                Accessible.ignored: true
            }
            Monogram {
                anchors.fill: parent
                visible: !row.hasIcon
                diameter: row.iconSize
                name: row.title
            }
            SequentialAnimation {
                id: pulse
                NumberAnimation { target: iconBox; property: "scale"; to: 1.22; duration: 80; easing.type: Easing.OutCubic }
                NumberAnimation { target: iconBox; property: "scale"; to: 1.0; duration: 100; easing.type: Easing.InOutQuad }
            }
            Connections {
                target: Launcher
                function onLaunchingChanged() { if (Launcher.launching && row.selected && !Style.reduceMotion) pulse.restart(); }
            }
        }

        Column {
            anchors.left: iconBox.right
            anchors.leftMargin: 14
            anchors.right: hintBox.left
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Text {
                width: parent.width
                text: row.title
                color: Style.text
                elide: Text.ElideRight
                font.family: "Inter"
                font.pixelSize: row.topHit ? 17 : 15
                font.weight: row.topHit ? Font.DemiBold : Font.Medium
                Accessible.ignored: true
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: row.subtitle
                color: Style.textSecondary
                elide: Text.ElideRight
                font.family: "Inter"
                font.pixelSize: 12
                Accessible.ignored: true
            }
        }

        Item {
            id: hintBox
            anchors.right: parent.right
            anchors.rightMargin: 20
            anchors.verticalCenter: parent.verticalCenter
            width: hintLabel.implicitWidth
            height: hintLabel.implicitHeight
            Text {
                id: hintLabel
                text: row.ctrlHeld && row.index < 9 ? qsTr("Ctrl+%1").arg(row.index + 1) : row.hintText
                color: row.hintText === qsTr("Press Enter to confirm") ? Style.warning : Style.textSecondary
                font.family: "Inter"
                font.pixelSize: 12
                font.weight: Font.Medium
                opacity: text.length > 0 ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Style.reduceMotion ? 0 : 100 } }
                Accessible.ignored: true
            }
        }

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            onPositionChanged: Launcher.hoverIndex(row.index)
            onClicked: Launcher.activateIndex(row.index)
            Accessible.ignored: true
        }
    }
}
