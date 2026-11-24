import QtQuick
import Taos.Dock

// One slot of the dock: an application, the separator, or a minimised window.
Item {
    id: slot
    required property int index
    required property string kind
    required property string name
    required property string icon
    required property bool running
    required property bool focused
    required property bool launching
    required property bool pinned
    required property string accessibleText
    required property string tooltip
    required property int minimisedCount
    property Item host
    property Item flowItem

    readonly property bool isSep: kind === "separator"
    readonly property bool isMin: kind === "minimised"
    width: Dock.vertical ? flowItem.width : (isSep ? Dock.separatorWidth : Dock.cell)
    height: Dock.vertical ? (isSep ? Dock.separatorWidth : Dock.cell) : flowItem.height

    readonly property bool kbd: Dock.keyboardActive && host && host.focusIndex === index
    activeFocusOnTab: false
    Accessible.role: isSep ? Accessible.Separator : Accessible.Button
    Accessible.name: accessibleText
    Accessible.ignored: isSep
    Accessible.focused: kbd
    Accessible.onPressAction: Dock.activate(index)
    Keys.onReturnPressed: Dock.activate(index)
    Keys.onEnterPressed: Dock.activate(index)
    Keys.onSpacePressed: Dock.activate(index)
    Keys.onMenuPressed: slot.openMenuHere(false)

    function centre() { return slot.mapToItem(host, slot.width / 2, slot.height / 2) }
    function openMenuHere(list) {
        var c = centre()
        Dock.hideTip()
        if (list) Dock.openWindowList(index, c.x, c.y); else Dock.openMenu(index, c.x, c.y)
    }

    // Separator
    Rectangle {
        visible: slot.isSep
        anchors.centerIn: parent
        width: Dock.vertical ? Dock.iconSize * 0.6 : 1
        height: Dock.vertical ? 1 : Dock.iconSize * 0.6
        color: Theme.hairline
    }

    Item {
        id: body
        visible: !slot.isSep
        anchors.fill: parent
        opacity: slot.launching ? pulse : 1
        property real pulse: 1
        SequentialAnimation on pulse {
            running: slot.launching && !Theme.reduceMotion
            loops: Animation.Infinite
            NumberAnimation { to: 0.45; duration: 600; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 600; easing.type: Easing.InOutSine }
        }
        // Magnification: grows towards the pointer, away from the screen edge. Off under reduced motion.
        readonly property real along: Dock.vertical ? slot.y + slot.height / 2 : slot.x + slot.width / 2
        readonly property real mag: (Dock.magnify && !Theme.reduceMotion && !slot.isSep && host.ptrMain > -5000)
            ? 1 + 0.55 * Math.exp(-Math.pow((along - host.ptrMain) / (Dock.cell * 1.25), 2)) : 1
        scale: mag * (press.pressed && !slot.dragging ? 0.92 : 1)
        transformOrigin: Dock.position === "bottom" ? Item.Bottom : Dock.position === "left" ? Item.Left : Item.Right
        z: slot.dragging ? 100 : mag
        transform: Translate { x: slot.dragging && !Dock.vertical ? slot.dragDelta : 0; y: slot.dragging && Dock.vertical ? slot.dragDelta : 0 }

        // The icon sits towards the outer side, the indicator dot on the inner edge.
        Item {
            id: iconBox
            width: Dock.iconSize
            height: Dock.iconSize
            x: Dock.vertical ? (Dock.position === "left" ? (slot.width - width) / 2 + 3 : (slot.width - width) / 2 - 3) : (slot.width - width) / 2
            y: Dock.vertical ? (slot.height - height) / 2 : (slot.height - height) / 2 - 3

            Rectangle {   // hover and focus plate
                anchors.fill: parent
                anchors.margins: -4
                radius: 10
                color: press.pressed ? Theme.pressed : press.containsMouse ? Theme.hover : "transparent"
                border.width: slot.kbd ? 2 : 0
                border.color: Theme.focusRing
            }
            Image {
                id: img
                anchors.fill: parent
                source: slot.icon !== "" ? "image://dockicon/" + slot.icon : ""
                sourceSize.width: Dock.iconSize * 2
                sourceSize.height: Dock.iconSize * 2
                fillMode: Image.PreserveAspectFit
                smooth: true
                asynchronous: false
                cache: true
                readonly property bool ok: status === Image.Ready && implicitWidth > 1
                visible: ok
            }
            Rectangle {   // bundled fallback glyph
                visible: !img.ok
                anchors.fill: parent
                anchors.margins: 3
                radius: Dock.iconSize / 4
                color: Theme.raised
                border.width: 1
                border.color: Theme.hairline
                Text {
                    anchors.centerIn: parent
                    text: slot.name.length > 0 ? slot.name.charAt(0).toUpperCase() : "?"
                    color: Theme.textSecondary
                    font.family: slot.host.fontFamily
                    font.pixelSize: Dock.iconSize * 0.42
                    font.weight: Font.DemiBold
                }
            }
            Rectangle {   // minimised badge: a small bar, bottom corner
                visible: slot.isMin
                width: 16
                height: 16
                radius: 8
                x: parent.width - 12
                y: parent.height - 12
                color: Theme.raised
                border.width: 1
                border.color: Theme.hairline
                Rectangle { anchors.centerIn: parent; width: 8; height: 2; radius: 1; color: Theme.text }
            }
        }
        Rectangle {   // running indicator: one dot, no accent colour
            visible: slot.running && !slot.isMin
            width: 4
            height: 4
            radius: 2
            color: slot.focused ? Theme.text : Theme.textSecondary
            x: Dock.vertical ? (Dock.position === "left" ? 3 : slot.width - 7) : (slot.width - 4) / 2
            y: Dock.vertical ? (slot.height - 4) / 2 : slot.height - 8
        }
    }

    MouseArea {
        id: press
        visible: !slot.isSep
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        property real startMain: 0
        function mainPos(m) { var p = press.mapToItem(slot.flowItem, m.x, m.y); return Dock.vertical ? p.y : p.x }
        onPressed: (m) => { if (m.button === Qt.LeftButton) { startMain = mainPos(m); slot.dragDelta = 0 } }
        onPositionChanged: (m) => {
            if (Dock.magnify) host.ptrMain = mainPos(m)   // hover moves too (hoverEnabled)
            if (!(pressedButtons & Qt.LeftButton) || !slot.pinned || slot.isMin) return
            var d = mainPos(m) - startMain
            if (!slot.dragging && Math.abs(d) > 10) { slot.dragging = true; tipTimer.stop(); Dock.hideTip() }
            if (slot.dragging) slot.dragDelta = d
        }
        onReleased: (m) => {
            if (!slot.dragging) return
            slot.dragging = false
            slot.longPressed = true   // swallow the click that follows a drag
            var target = slot.index + Math.round(slot.dragDelta / Dock.cell)
            target = Math.max(0, Math.min(Dock.model.pinnedCount - 1, target))
            slot.dragDelta = 0
            if (target !== slot.index) Dock.movePinned(slot.index, target)
        }
        onClicked: (m) => {
            if (m.button === Qt.RightButton) slot.openMenuHere(false)
            else if (m.button === Qt.MiddleButton) Dock.middleClick(slot.index)
            else if (!slot.longPressed) Dock.activate(slot.index)
            slot.longPressed = false
        }
        onPressAndHold: (m) => { if (m.button === Qt.LeftButton && !slot.dragging) { slot.longPressed = true; slot.openMenuHere(true) } }
        onContainsMouseChanged: {
            if (containsMouse) { tipTimer.restart() }
            else { tipTimer.stop(); Dock.hideTip() }
        }
        Timer {
            id: tipTimer
            interval: 450
            onTriggered: { var c = slot.centre(); Dock.showTip(slot.tooltip, c.x, c.y) }
        }
    }
    property bool longPressed: false
    property bool dragging: false
    property real dragDelta: 0
}
