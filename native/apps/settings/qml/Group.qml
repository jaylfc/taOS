// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Style 1.0

// A group of rows inside one hairline outline: no nested cards.
Item {
    id: g
    property string heading: ""
    property string footnote: ""
    default property alias rows: col.data
    width: parent ? parent.width : 400
    implicitHeight: (head.visible ? head.height + 8 : 0) + box.height + (foot.visible ? foot.height + 8 : 0)

    Txt {
        id: head
        visible: g.heading.length > 0
        text: g.heading
        width: parent.width
        font.pixelSize: 12
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.6
        color: TaosStyle.textSecondary
        leftPadding: 4
        Accessible.role: Accessible.Heading
    }
    Rectangle {
        id: box
        y: head.visible ? head.height + 8 : 0
        width: parent.width
        height: col.implicitHeight
        radius: 10
        color: TaosStyle.surface
        clip: true
        Column { id: col; width: parent.width }
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: 1
            border.color: TaosStyle.hairline
        }
    }
    Txt {
        id: foot
        visible: g.footnote.length > 0
        y: box.y + box.height + 8
        text: g.footnote
        width: parent.width
        font.pixelSize: 12
        color: TaosStyle.textTertiary
        leftPadding: 4
    }
}
