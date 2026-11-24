// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock

// One button of the taskbar: the start button, a window, or the "more windows" button. The look follows the skin:
// with dockSurface "raised" it is a bevelled key that sinks while it is the active window or pressed; otherwise a
// flat plate with a line under the active window. Text and icon use the theme tokens only.
Item {
    id: btn
    property string label: ""
    property string iconName: ""
    property bool active: false          // the focused window
    property bool minimised: false
    property bool kbd: false             // shows the keyboard focus ring
    property bool bold: false
    property bool showIcon: true
    property string accessibleText: label
    property string fontFamily: Theme.fontStack
    property alias hovered: mouse.containsMouse
    readonly property bool pressed: mouse.pressed
    readonly property bool raised: Theme.dockSurface === "raised"
    readonly property bool sunk: pressed || (active && raised)
    readonly property int bw: Math.max(1, Theme.borderWidth)
    default property alias extra: extraSlot.data
    signal activated()
    signal context()

    Accessible.role: Accessible.Button
    Accessible.name: accessibleText
    Accessible.focused: kbd
    Accessible.onPressAction: btn.activated()

    // face
    Rectangle {
        anchors.fill: parent
        radius: Math.round((raised ? 0 : 6) * Theme.radiusScale)
        color: btn.raised ? (btn.sunk ? Qt.tint(Theme.surface, Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.10)) : Theme.surface)
                          : (btn.pressed ? Theme.pressed : btn.active ? Theme.selection : btn.hovered ? Theme.hover : "transparent")
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 90 } }
    }
    // bevel
    Item {
        anchors.fill: parent
        visible: btn.raised && Theme.borderWidth > 0
        Rectangle { x: 0; y: 0; width: parent.width; height: btn.bw; color: btn.sunk ? Theme.bevelDark : Theme.bevelLight }
        Rectangle { x: 0; y: 0; width: btn.bw; height: parent.height; color: btn.sunk ? Theme.bevelDark : Theme.bevelLight }
        Rectangle { x: 0; y: parent.height - btn.bw; width: parent.width; height: btn.bw; color: btn.sunk ? Theme.bevelLight : Theme.bevelDark }
        Rectangle { x: parent.width - btn.bw; y: 0; width: btn.bw; height: parent.height; color: btn.sunk ? Theme.bevelLight : Theme.bevelDark }
    }
    // flat style: the line under the active window
    Rectangle {
        visible: !btn.raised && btn.active
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Math.round((6) * Theme.spacingFactor)
        anchors.rightMargin: Math.round((6) * Theme.spacingFactor)
        height: 2
        color: Theme.accent
    }
    Item {
        id: content
        anchors.fill: parent
        anchors.leftMargin: Math.round((8) * Theme.spacingFactor) + (btn.sunk ? 1 : 0)
        anchors.rightMargin: Math.round((8) * Theme.spacingFactor)
        anchors.topMargin: btn.sunk ? 1 : 0
        Item {
            id: extraSlot
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: childrenRect.width
            height: childrenRect.height
        }
        Image {
            id: ico
            readonly property bool shown: btn.showIcon && btn.iconName !== "" && status === Image.Ready
            anchors.left: extraSlot.right
            anchors.leftMargin: extraSlot.width > 0 ? Math.round((8) * Theme.spacingFactor) : 0
            anchors.verticalCenter: parent.verticalCenter
            width: shown ? Math.min(20, btn.height - 10) : 0
            height: width
            visible: shown
            source: btn.iconName !== "" ? "image://dockicon/" + btn.iconName : ""
            sourceSize.width: 40
            sourceSize.height: 40
            fillMode: Image.PreserveAspectFit
            smooth: true
            opacity: btn.minimised ? 0.6 : 1
        }
        Text {
            id: txt
            visible: btn.label !== ""
            anchors.left: ico.right
            anchors.leftMargin: ico.width > 0 || extraSlot.width > 0 ? Math.round((8) * Theme.spacingFactor) : 0
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignLeft
            text: btn.label
            elide: Text.ElideRight
            color: btn.minimised ? Theme.textSecondary : Theme.text
            font.family: btn.fontFamily
            font.pixelSize: Theme.fontSize
            font.weight: btn.active || btn.bold ? Font.DemiBold : Font.Medium
            renderType: Text.NativeRendering
        }
    }
    // keyboard focus ring
    Rectangle {
        visible: btn.kbd
        anchors.fill: parent
        anchors.margins: btn.raised ? 3 : 1
        color: "transparent"
        radius: Math.round((btn.raised ? 0 : 5) * Theme.radiusScale)
        border.width: 2
        border.color: Theme.focusRing
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: Qt.ArrowCursor
        onClicked: (m) => { if (m.button === Qt.RightButton) btn.context(); else btn.activated() }
    }
}
