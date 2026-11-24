// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls
import Taos.Style 1.0

// A themed drop-down for the model-activity filters. `entries` is a list of
// {text, value}; the first is the "all" entry with value "".
ComboBox {
    id: box
    property string current: ""
    property string name: ""
    signal picked(string value)
    textRole: "text"
    valueRole: "value"
    implicitHeight: 30
    implicitWidth: 140
    font.family: Pal.family
    font.pixelSize: 12
    currentIndex: {
        var m = model
        for (var i = 0; i < m.length; ++i) if (m[i].value === current) return i
        return 0
    }
    onActivated: function (i) { picked(model[i].value) }
    Accessible.name: name
    palette.window: TaosStyle.surface
    palette.base: TaosStyle.raised
    palette.text: TaosStyle.text
    palette.windowText: TaosStyle.text
    palette.button: TaosStyle.raised
    palette.buttonText: TaosStyle.text
    palette.highlight: TaosStyle.selection
    palette.highlightedText: TaosStyle.text
    palette.mid: Pal.hairline
    background: Rectangle {
        radius: 8
        color: box.hovered ? Pal.glassFillHover : Pal.glassFill
        border.width: box.visualFocus ? 2 : 1
        border.color: box.visualFocus ? Pal.focusRing : Pal.hairline
    }
    contentItem: Text {
        leftPadding: 10
        rightPadding: 22
        text: box.displayText
        font: box.font
        color: TaosStyle.text
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        renderType: Text.QtRendering
    }
}
