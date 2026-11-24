import QtQuick
import Taos.Style 1.0

// A small pill: status, tier, capability, backend, host.
Rectangle {
    id: chip
    property alias text: label.text
    property color tone: TaosStyle.textSecondary
    property bool outlined: false
    property bool mono: false
    implicitHeight: 20
    implicitWidth: label.implicitWidth + 16
    radius: height / 2
    color: outlined ? "transparent" : Pal.alpha(tone, 0.16)
    border.width: outlined ? 1 : 0
    border.color: Pal.hairline
    Txt {
        id: label
        anchors.centerIn: parent
        width: Math.min(implicitWidth, chip.width - 16)
        font.pixelSize: 11
        font.weight: Font.Medium
        font.family: chip.mono ? "monospace" : Pal.family
        color: outlined ? TaosStyle.textTertiary : tone
    }
}
