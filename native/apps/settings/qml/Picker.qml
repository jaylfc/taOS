// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Style 1.0

// A drop down list. `model` is a list of strings, objects or a list model; `textRole` names the label.
C.ComboBox {
    id: pick
    property string label: ""
    implicitWidth: 220
    implicitHeight: 30
    leftPadding: 12; rightPadding: 28
    Accessible.name: label
    Accessible.role: Accessible.ComboBox
    background: Rectangle {
        radius: 7
        color: pick.pressed ? TaosStyle.selection : (pick.hovered ? TaosStyle.raised : TaosStyle.track)
        border.width: pick.visualFocus ? 2 : 1
        border.color: pick.visualFocus ? TaosStyle.focusRing : TaosStyle.hairline
    }
    contentItem: Txt {
        text: pick.displayText
        elide: Text.ElideRight
        wrapMode: Text.NoWrap
        verticalAlignment: Text.AlignVCenter
        color: pick.enabled ? TaosStyle.text : TaosStyle.textTertiary
    }
    indicator: Icon {
        x: pick.width - width - 8; y: (pick.height - height) / 2
        size: 12; name: "back"; rotation: -90
        color: TaosStyle.textSecondary
    }
    delegate: C.ItemDelegate {
        id: d
        required property var model
        required property int index
        width: pick.width
        height: 32
        highlighted: pick.highlightedIndex === index
        Accessible.name: pick.textRole.length ? String(model[pick.textRole]) : String(model.modelData)
        contentItem: Txt {
            text: pick.textRole.length ? String(d.model[pick.textRole]) : String(d.model.modelData)
            elide: Text.ElideRight
            wrapMode: Text.NoWrap
            verticalAlignment: Text.AlignVCenter
            font.weight: pick.currentIndex === d.index ? Font.DemiBold : Font.Normal
        }
        background: Rectangle { color: d.highlighted ? TaosStyle.selection : "transparent" }
    }
    popup: C.Popup {
        y: pick.height + 4
        width: Math.max(pick.width, 240)
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 300)
        padding: 4
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: pick.popup.visible ? pick.delegateModel : null
            currentIndex: pick.highlightedIndex
        }
        background: Rectangle {
            radius: 9
            color: TaosStyle.raised
            border.width: 1
            border.color: TaosStyle.hairline
        }
    }
}
