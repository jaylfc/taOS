import QtQuick
import QtQuick.Effects
import Taos.Dock

// The dock strip. The surface is exactly the strip (plus magnification headroom when that is on); the strip
// slides along its cross axis to hide. Shadow and blur come from the compositor (layer_effects). Colours come from Theme only.
Item {
    id: root
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    readonly property bool dark: Theme.variant !== "light"
    readonly property bool hidden: Dock.autohide && !Dock.revealed

    Accessible.role: Accessible.ToolBar
    Accessible.name: "Dock"

    // Pointer position along the strip (in the item flow's coordinates), for magnification.
    property real ptrMain: -10000
    HoverHandler {
        id: hover
        onHoveredChanged: { Dock.setPointerInside(hovered); if (!hovered) root.ptrMain = -10000 }
        onPointChanged: {
            if (!hovered) return
            var p = flow.mapFromItem(root, point.position.x, point.position.y)
            root.ptrMain = Dock.vertical ? p.y : p.x
        }
    }

    // Keyboard (Super+D gives the dock the keyboard): arrows move, Enter activates, Menu opens the menu, Escape leaves.
    property int focusIndex: -1
    focus: Dock.keyboardActive
    function usable(i) { var it = items.itemAt(i); return it && !it.isSep }
    function move(d) {
        var n = items.count
        if (n === 0) return
        var i = focusIndex
        for (var k = 0; k < n; ++k) {
            i = (i + d + n) % n
            if (usable(i)) { focusIndex = i; return }
        }
    }
    Connections {
        target: Dock
        function onKeyboardActiveChanged() {
            if (Dock.keyboardActive) { root.forceActiveFocus(); root.focusIndex = -1; root.move(1) }
            else root.focusIndex = -1
        }
    }
    Keys.onLeftPressed: if (!Dock.vertical) move(-1)
    Keys.onRightPressed: if (!Dock.vertical) move(1)
    Keys.onUpPressed: if (Dock.vertical) move(-1)
    Keys.onDownPressed: if (Dock.vertical) move(1)
    Keys.onTabPressed: move(1)
    Keys.onBacktabPressed: move(-1)
    Keys.onPressed: (e) => {
        if (e.key === Qt.Key_Home) { focusIndex = -1; move(1); e.accepted = true }
        else if (e.key === Qt.Key_End) { focusIndex = 0; move(-1); e.accepted = true }
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) { if (focusIndex >= 0) Dock.activate(focusIndex); e.accepted = true }
        else if (e.key === Qt.Key_Menu) { var it = items.itemAt(focusIndex); if (it) it.openMenuHere(false); e.accepted = true }
        else if (e.key === Qt.Key_Escape) { Dock.releaseKeyboard(); e.accepted = true }
    }

    Item {
        id: strip
        x: Dock.stripRect.x
        y: Dock.stripRect.y
        width: Dock.stripRect.width
        height: Dock.stripRect.height
        property real slide: root.hidden ? Dock.slideDistance : 0
        Behavior on slide { enabled: !Theme.reduceMotion; NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        transform: Translate {
            x: Dock.position === "left" ? -strip.slide : Dock.position === "right" ? strip.slide : 0
            y: Dock.position === "bottom" ? strip.slide : 0
        }
        opacity: root.hidden && Theme.reduceMotion ? 0 : 1

        // Glass: tint, veil, hairline (the bar menus' recipe; the compositor adds blur and shadow).
        Rectangle {
            id: tint
            anchors.fill: parent
            radius: Dock.radius
            readonly property bool flatBottom: Dock.attached && Dock.position === "bottom"
            readonly property bool flatLeft: Dock.attached && Dock.position === "left"
            readonly property bool flatRight: Dock.attached && Dock.position === "right"
            topLeftRadius: flatLeft ? 0 : Dock.radius
            bottomLeftRadius: flatLeft || flatBottom ? 0 : Dock.radius
            topRightRadius: flatRight ? 0 : Dock.radius
            bottomRightRadius: flatRight || flatBottom ? 0 : Dock.radius
            color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, root.dark ? 0.86 : 0.90)
            border.width: 1
            border.color: Theme.glassStroke
        }
        Rectangle {
            anchors.fill: parent
            radius: Dock.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Theme.glassVeil }
                GradientStop { position: 1.0; color: Qt.rgba(Theme.glassVeil.r, Theme.glassVeil.g, Theme.glassVeil.b, Theme.glassVeil.a * 0.22) }
            }
        }

        Flow {
            id: flow
            z: 1
            flow: Dock.vertical ? Flow.TopToBottom : Flow.LeftToRight
            x: Dock.vertical ? 0 : Dock.padding
            y: Dock.vertical ? Dock.padding : 0
            width: Dock.vertical ? parent.width : parent.width - 2 * Dock.padding
            height: Dock.vertical ? parent.height - 2 * Dock.padding : parent.height
            Repeater {
                id: items
                model: Dock.model
                delegate: DockItem {
                    host: root
                    flowItem: flow
                }
            }
        }
    }
}
