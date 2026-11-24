import QtQuick
import QtQuick.Controls
import Taos.Style 1.0

// A quiet glass button with hover, press and a visible keyboard focus ring.
Button {
    id: b
    property bool prominent: false
    property color tone: Pal.accent
    property bool danger: false
    implicitHeight: 32
    leftPadding: 14
    rightPadding: 14
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Pal.family
    font.pixelSize: 13
    contentItem: Text {
        text: b.text
        font.family: b.font.family
        font.pixelSize: b.font.pixelSize
        font.weight: Font.Medium
        color: !b.enabled ? TaosStyle.textTertiary
             : b.prominent ? TaosStyle.accentText
             : (b.danger ? TaosStyle.danger : TaosStyle.text)
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        renderType: Text.QtRendering
    }
    background: Rectangle {
        radius: 8
        color: b.prominent ? (b.danger ? TaosStyle.danger : b.tone)
             : b.down ? Pal.alpha(TaosStyle.text, 0.16)
             : b.hovered ? Pal.glassFillHover : Pal.glassFill
        opacity: b.prominent && b.down ? 0.85 : 1
        border.width: b.visualFocus ? 2 : 1
        border.color: b.visualFocus ? Pal.focusRing : Pal.hairline
        Behavior on color { ColorAnimation { duration: Pal.dur(120) } }
    }
}
