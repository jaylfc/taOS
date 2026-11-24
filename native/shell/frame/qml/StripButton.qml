// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Frame

// One window button. The shape comes from the skin (Theme.buttonShape): "round" is a monochrome disc, "square" is a
// bevelled key, "glyph" is the bare line glyph (a short line, a square outline, a rotated square outline for close, in
// the danger colour). Whatever the shape, the glyph is always drawn (focused window: strong, unfocused: dim); hover and
// pressed lift the shape. `danger` turns the close button to the danger colour on hover. A button the window cannot use
// (full size on a dialog) is an inert, empty shape.
// Kept in step with native/qt/style/Taos/Style/TitleButton.qml (the strip cannot import the foundation module).
Item {
    id: btn
    property string glyph: "close"        // close | minimise | full
    property string label: ""
    property bool groupHovered: false      // kept for callers; the glyph no longer waits for it
    property bool danger: false
    property bool usable: true
    property bool windowActive: true
    signal activated()

    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: btn.activated()
    readonly property bool hovered: hover.hovered && usable
    readonly property bool pressed: mouse.pressed && usable
    readonly property string shape: Theme.buttonShape
    readonly property int size: Theme.buttonSize
    readonly property real stroke: Math.max(1, Theme.iconStroke * 0.9)
    // on an accent title bar the ink is the text colour of the accent
    readonly property color inkBase: windowActive && Theme.activeTitle === "accent" ? Theme.accentText : Theme.text
    readonly property bool inkShown: usable

    // round
    Rectangle {
        visible: btn.shape === "round"
        anchors.centerIn: parent
        width: btn.size
        height: btn.size
        radius: width / 2
        color: !btn.usable ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.08)
             : btn.hovered && btn.danger ? (btn.pressed ? Qt.darker(Theme.danger, 1.2) : Theme.danger)
             : (btn.hovered || btn.pressed ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.pressed ? 0.60 : 0.46)
                                           : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.windowActive ? 0.26 : 0.12))
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 90 } }
    }
    // square: a bevelled key, sunk while pressed
    Item {
        id: key
        visible: btn.shape === "square"
        anchors.centerIn: parent
        width: btn.size + 2
        height: btn.size + 2
        readonly property bool sunk: btn.pressed
        readonly property int bw: Math.max(1, Theme.borderWidth)
        Rectangle {
            anchors.fill: parent
            radius: Math.round((2) * Theme.radiusScale)
            color: key.sunk ? Qt.tint(Theme.surface, Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.12))
                 : btn.hovered ? Theme.raised : Theme.surface
        }
        Rectangle { x: 0; y: 0; width: parent.width; height: key.bw; color: key.sunk ? Theme.bevelDark : Theme.bevelLight }
        Rectangle { x: 0; y: 0; width: key.bw; height: parent.height; color: key.sunk ? Theme.bevelDark : Theme.bevelLight }
        Rectangle { x: 0; y: parent.height - key.bw; width: parent.width; height: key.bw; color: key.sunk ? Theme.bevelLight : Theme.bevelDark }
        Rectangle { x: parent.width - key.bw; y: 0; width: key.bw; height: parent.height; color: key.sunk ? Theme.bevelLight : Theme.bevelDark }
    }
    // hover plate for the bare glyphs
    Rectangle {
        visible: btn.shape === "glyph"
        anchors.centerIn: parent
        width: btn.size + 6
        height: btn.size + 6
        color: btn.hovered ? (btn.danger ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.22) : Theme.hover) : "transparent"
    }

    // glyphs
    Item {
        id: ink
        anchors.centerIn: parent
        width: 8
        height: 8
        visible: btn.inkShown
        opacity: btn.windowActive || btn.hovered ? 1 : 0.55
        readonly property color col: btn.shape === "glyph" && btn.glyph === "close" ? Theme.danger
            : (btn.hovered && btn.danger && btn.shape === "round" ? Theme.ground : btn.inkBase)
        // classic: a cross for close
        Rectangle { visible: btn.glyph === "close" && btn.shape !== "glyph"; anchors.centerIn: parent; width: 9; height: btn.stroke; rotation: 45; color: ink.col; antialiasing: true }
        Rectangle { visible: btn.glyph === "close" && btn.shape !== "glyph"; anchors.centerIn: parent; width: 9; height: btn.stroke; rotation: -45; color: ink.col; antialiasing: true }
        // bare glyph: a small rotated square outline
        Rectangle { visible: btn.glyph === "close" && btn.shape === "glyph"; anchors.centerIn: parent; width: 8; height: 8; rotation: 45; color: "transparent"; border.width: btn.stroke; border.color: ink.col }
        // minimise: a line at the baseline
        Rectangle { visible: btn.glyph === "minimise"; x: 0; y: btn.shape === "glyph" ? 7 : 5; width: 8; height: btn.stroke; color: ink.col }
        // full size: a square outline
        Rectangle { visible: btn.glyph === "full"; anchors.centerIn: parent; width: 7; height: 7; color: "transparent"; border.width: btn.stroke; border.color: ink.col }
    }
    HoverHandler { id: hover; cursorShape: Qt.ArrowCursor }
    onHoveredChanged: btn.hoverChanged(hovered)
    signal hoverChanged(bool over)
    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: if (btn.usable) btn.activated()
        onPressed: btn.hoverChanged(false)
    }
}
