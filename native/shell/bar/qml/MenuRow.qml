// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// One row of the menu: leading glyph, title and subtitle, trailing glyph, and
// an optional small action (Forget, Disconnect, Pair) that shows on hover or
// focus. Enter or Space activates; Delete runs the action.
Item {
    id: row
    property string title: ""
    property string subtitle: ""
    property string accessibleName: title
    property string accessibleHint: ""
    property bool emphasized: false
    property string actionText: ""
    property string actionName: actionText
    property bool actionAlways: false
    property bool interactive: true
    default property alias leading: leadSlot.data
    property alias trailing: trailSlot.data
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    signal activated()
    signal actionTriggered()

    width: parent ? parent.width : 320
    height: subtitle.length > 0 ? 44 : 34
    activeFocusOnTab: interactive

    Accessible.role: interactive ? Accessible.Button : Accessible.StaticText
    Accessible.name: accessibleName
    Accessible.description: accessibleHint
    Accessible.focusable: interactive
    Accessible.onPressAction: row.activated()

    HoverHandler { id: hover; enabled: row.interactive }
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: !row.interactive ? "transparent" : tap.pressed ? Theme.pressed : (hover.hovered || row.activeFocus) ? Theme.hover : "transparent"
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 100 } }
    }
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: "transparent"
        border.width: 1.5
        border.color: Theme.text
        opacity: row.activeFocus ? 0.4 : 0
    }
    MouseArea {
        id: tap
        anchors.fill: parent
        enabled: row.interactive
        onClicked: row.activated()
    }
    Item {
        id: leadSlot
        x: 10
        width: 22
        height: parent.height
    }
    Column {
        anchors.left: parent.left
        anchors.leftMargin: 42
        anchors.right: trailRow.left
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1
        Text {
            width: parent.width
            text: row.title
            elide: Text.ElideRight
            color: Theme.text
            font.family: row.fontFamily
            font.pixelSize: 13
            font.weight: row.emphasized ? Font.DemiBold : Font.Medium
            renderType: Text.NativeRendering
        }
        Text {
            visible: row.subtitle.length > 0
            width: parent.width
            text: row.subtitle
            elide: Text.ElideRight
            color: Theme.textSecondary
            font.family: row.fontFamily
            font.pixelSize: 11
            font.features: { "tnum": 1 }
            renderType: Text.NativeRendering
        }
    }
    Row {
        id: trailRow
        anchors.right: parent.right
        anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Rectangle {
            id: actionBtn
            visible: row.actionText.length > 0 && (row.actionAlways || hover.hovered || row.activeFocus)
            anchors.verticalCenter: parent.verticalCenter
            width: actionLabel.implicitWidth + 18
            height: 22
            radius: 11
            color: actionTap.pressed ? Theme.pressed : actionTap.containsMouse ? Theme.hover : "transparent"
            border.width: 1
            border.color: Theme.stroke
            Accessible.role: Accessible.Button
            Accessible.name: row.actionName
            Accessible.onPressAction: row.actionTriggered()
            Text {
                id: actionLabel
                anchors.centerIn: parent
                text: row.actionText
                color: Theme.text
                font.family: row.fontFamily
                font.pixelSize: 11
                font.weight: Font.Medium
                renderType: Text.NativeRendering
            }
            MouseArea {
                id: actionTap
                anchors.fill: parent
                hoverEnabled: true
                onClicked: row.actionTriggered()
            }
        }
        Item {
            id: trailSlot
            anchors.verticalCenter: parent.verticalCenter
            width: childrenRect.width
            height: 16
        }
    }
    Keys.onReturnPressed: if (interactive) row.activated()
    Keys.onEnterPressed: if (interactive) row.activated()
    Keys.onSpacePressed: if (interactive) row.activated()
    Keys.onDeletePressed: if (actionText.length > 0) row.actionTriggered()
}
