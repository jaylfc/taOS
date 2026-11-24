import QtQuick
import Taos.Style 1.0

// One round window button: monochrome, 14 px disc in a 24 px hit area. The glyph shows while the pointer is
// over the group of three or while the button has keyboard focus. `danger` turns the disc to the theme's
// danger colour on hover (close).
Item {
    id: btn
    property string glyph: "close"        // close | minimise | full
    property string label: ""
    property bool groupHovered: false
    property bool danger: false
    property bool windowActive: true
    signal activated()

    implicitWidth: 24
    implicitHeight: 24
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: btn.activated()
    readonly property bool hovered: hover.hovered
    readonly property bool showGlyph: groupHovered || btn.activeFocus

    Rectangle {
        id: disc
        anchors.centerIn: parent
        width: 14
        height: 14
        radius: 7
        color: btn.hovered && btn.danger ? TaosStyle.danger
             : (btn.hovered || mouse.pressed ? Qt.rgba(TaosStyle.text.r, TaosStyle.text.g, TaosStyle.text.b, mouse.pressed ? 0.42 : 0.34)
                                             : Qt.rgba(TaosStyle.text.r, TaosStyle.text.g, TaosStyle.text.b, btn.windowActive ? 0.22 : 0.12))
        Behavior on color { enabled: !TaosStyle.reduceMotion; ColorAnimation { duration: 90 } }
        // The glyphs are drawn from rectangles: nothing to load, and they follow the theme.
        Item {
            anchors.centerIn: parent
            width: 8
            height: 8
            visible: btn.showGlyph
            readonly property color ink: btn.hovered && btn.danger ? TaosStyle.ground : TaosStyle.text
            Rectangle { visible: btn.glyph === "close"; anchors.centerIn: parent; width: 9; height: 1.4; rotation: 45; color: parent.ink; antialiasing: true }
            Rectangle { visible: btn.glyph === "close"; anchors.centerIn: parent; width: 9; height: 1.4; rotation: -45; color: parent.ink; antialiasing: true }
            Rectangle { visible: btn.glyph === "minimise"; anchors.centerIn: parent; width: 8; height: 1.4; color: parent.ink }
            Rectangle {
                visible: btn.glyph === "full"
                anchors.centerIn: parent
                width: 7
                height: 7
                color: "transparent"
                border.width: 1.4
                border.color: parent.ink
            }
        }
    }
    Rectangle {
        anchors.centerIn: parent
        width: 20
        height: 20
        radius: 10
        color: "transparent"
        border.width: 2
        border.color: TaosStyle.focusRing
        visible: btn.activeFocus && btn.focusReason !== Qt.MouseFocusReason
    }
    property int focusReason: Qt.OtherFocusReason
    onActiveFocusChanged: if (!activeFocus) focusReason = Qt.OtherFocusReason

    HoverHandler { id: hover; cursorShape: Qt.ArrowCursor }
    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onPressed: { btn.focusReason = Qt.MouseFocusReason }
        onClicked: btn.activated()
    }
    Keys.onPressed: (e) => {
        if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) {
            btn.activated()
            e.accepted = true
        }
    }
}
