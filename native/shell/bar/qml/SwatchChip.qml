import QtQuick
import Taos.Bar

// One theme in the picker: its three swatches (ground, surface, accent) over its name,
// marked with a check when it is the current one. A radio button for assistive tech;
// Enter or Space picks it, Left and Right move between the themes.
Item {
    id: chip
    property string name: ""
    property string description: ""
    property var swatches: []
    property bool checked: false
    property string accessibleName: checked ? qsTr("%1, selected").arg(name) : name
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    signal activated()

    implicitWidth: 96
    implicitHeight: 62
    width: implicitWidth
    height: implicitHeight
    activeFocusOnTab: true

    Accessible.role: Accessible.RadioButton
    Accessible.name: chip.accessibleName
    Accessible.description: chip.description
    Accessible.checkable: true
    Accessible.checked: chip.checked
    Accessible.focusable: true
    Accessible.onPressAction: chip.activated()
    Accessible.onToggleAction: chip.activated()

    function step(d) {
        var kids = chip.parent ? chip.parent.children : []
        var i = -1
        for (var k = 0; k < kids.length; ++k) if (kids[k] === chip) i = k
        for (var j = i + d; j >= 0 && j < kids.length; j += d)
            if (kids[j].activeFocusOnTab === true && kids[j].visible) { kids[j].forceActiveFocus(); return }
    }

    HoverHandler { id: hover }
    Rectangle {
        anchors.fill: parent
        radius: 10
        color: tap.pressed ? Theme.pressed : chip.checked ? Theme.hover : hover.hovered ? Theme.hover : "transparent"
        border.width: chip.checked ? 1.5 : 1
        border.color: chip.checked ? Theme.text : Theme.stroke
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -2.5
        radius: 12
        color: "transparent"
        border.width: 1.5
        border.color: Theme.text
        opacity: chip.activeFocus ? 0.4 : 0
    }
    Row {
        id: dots
        anchors.horizontalCenter: parent.horizontalCenter
        y: 10
        spacing: -4
        Repeater {
            model: chip.swatches ? chip.swatches.length : 0
            delegate: Rectangle {
                required property int index
                width: 22; height: 22; radius: 11
                color: chip.swatches[index]
                border.width: 1
                border.color: Theme.stroke
                Accessible.ignored: true
            }
        }
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 38
        width: Math.min(implicitWidth, chip.width - 8)
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        text: chip.name
        color: chip.checked ? Theme.text : Theme.textSecondary
        font.family: chip.fontFamily
        font.pixelSize: 11
        font.weight: chip.checked ? Font.DemiBold : Font.Medium
        Accessible.ignored: true
    }
    CheckGlyph {
        visible: chip.checked
        width: 12; height: 12
        x: chip.width - 18
        y: 6
        color: Theme.text
        Accessible.ignored: true
    }
    MouseArea { id: tap; anchors.fill: parent; onClicked: chip.activated() }
    Keys.onReturnPressed: chip.activated()
    Keys.onEnterPressed: chip.activated()
    Keys.onSpacePressed: chip.activated()
    Keys.onLeftPressed: chip.step(-1)
    Keys.onRightPressed: chip.step(1)
}
