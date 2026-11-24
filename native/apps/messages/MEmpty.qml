// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// (Messages copy of the shared EmptyState: the texts fill the width so they wrap inside a narrow pane.)
// A calm empty (or error) state: a glyph, a headline and a line of help.
ColumnLayout {
    id: root
    property string glyph: "bell"
    property string title: ""
    property string detail: ""
    property string tone: "neutral"
    spacing: 8
    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        width: 56; height: 56; radius: 28
        color: root.tone === "neutral" ? Look.field : Look.tint(Look.tone(root.tone), 0.14)
        border.width: 1
        border.color: root.tone === "neutral" ? Look.hairline : Look.tint(Look.tone(root.tone), 0.3)
        Glyph { anchors.centerIn: parent; name: root.glyph; size: 24; stroke: 1.6
                color: root.tone === "neutral" ? Look.textTertiary : Look.tone(root.tone) }
    }
    TaosText {
        Layout.alignment: Qt.AlignHCenter
        Layout.fillWidth: true
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: root.title
        font.pixelSize: Look.title
        font.weight: Font.DemiBold
        Accessible.ignored: false
        Accessible.role: Accessible.StaticText
        Accessible.name: root.title + (root.detail !== "" ? ". " + root.detail : "")
    }
    TaosText {
        visible: root.detail !== ""
        Layout.alignment: Qt.AlignHCenter
        Layout.fillWidth: true
        Layout.maximumWidth: 320
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: root.detail
        color: Look.textSecondary
        font.pixelSize: Look.body
    }
}
