// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Style 1.0

// A push button. `kind`: "normal", "accent" (the one main action), "danger".
C.Button {
    id: b
    property string kind: "normal"
    implicitHeight: 30
    implicitWidth: Math.max(72, label.implicitWidth + 28)
    leftPadding: 14; rightPadding: 14
    Accessible.name: text
    background: Rectangle {
        radius: 7
        color: !b.enabled ? TaosStyle.track
             : b.kind === "accent" ? (b.down ? Qt.darker(TaosStyle.accent, 1.15) : TaosStyle.accent)
             : (b.down ? TaosStyle.selection : (b.hovered ? TaosStyle.raised : TaosStyle.track))
        border.width: b.visualFocus ? 2 : 1
        border.color: b.visualFocus ? TaosStyle.focusRing : TaosStyle.hairline
        Behavior on color { ColorAnimation { duration: TaosStyle.reduceMotion ? 0 : 120 } }
    }
    contentItem: Txt {
        id: label
        text: b.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
        color: !b.enabled ? TaosStyle.textTertiary
             : b.kind === "accent" ? TaosStyle.accentText
             : b.kind === "danger" ? TaosStyle.danger : TaosStyle.text
        font.weight: Font.Medium
    }
}
