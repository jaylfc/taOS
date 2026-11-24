// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// The event rows of the model-activity feed.
ListView {
    id: list
    property var feed
    property double nowSec: 0
    property bool compact: false
    clip: true
    spacing: 2
    boundsBehavior: Flickable.StopAtBounds
    Accessible.role: Accessible.List
    Accessible.name: qsTr("Model activity events")
    model: feed.model
    ScrollBar.vertical: ScrollBar { }
    add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.dur(260) } }
    displaced: Transition { NumberAnimation { property: "y"; duration: Pal.dur(220); easing.type: Easing.OutCubic } }
    delegate: Item {
        id: row
        required property int seq
        required property real ts
        required property string event
        required property string model
        required property string worker
        required property var duration_ms
        required property var tokens_out
        required property var token_rate
        width: ListView.view.width
        height: 28
        readonly property var ev: ({ tokens_out: tokens_out, token_rate: token_rate })
        readonly property string kind: Fmt.modelKind(model)
        readonly property string glyph: kind === "vector" ? "∷" : kind === "image" ? "▣" : kind === "audio" ? "♪"
                                      : kind === "vision" ? "◎" : kind === "code" ? "‹›" : "◉"
        readonly property color tone: Pal.tone(Fmt.eventTone(event))
        Accessible.role: Accessible.ListItem
        Accessible.name: (model || qsTr("unknown")) + ", " + Fmt.eventLabel(event) + ", " + worker + ", " + Fmt.relative(ts, list.nowSec)
        RowLayout {
            anchors.fill: parent
            spacing: 10
            Txt { text: row.glyph; color: TaosStyle.textTertiary; font.pixelSize: 12; Layout.preferredWidth: 18; horizontalAlignment: Text.AlignHCenter }
            Txt { text: row.model || qsTr("unknown"); font.pixelSize: 12; Layout.fillWidth: true; Layout.minimumWidth: 80 }
            Txt { visible: !list.compact; text: row.worker; font.pixelSize: 12; color: TaosStyle.textTertiary; Layout.preferredWidth: 96 }
            Chip { text: Fmt.eventLabel(row.event); tone: row.tone; implicitHeight: 20 }
            Txt {
                visible: !list.compact
                text: row.event === "request.finish" && row.duration_ms !== null ? Fmt.duration(row.duration_ms) : ""
                font.pixelSize: 12; color: TaosStyle.textTertiary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 64
            }
            Txt {
                visible: !list.compact
                text: Fmt.tokens(row.ev)
                font.pixelSize: 12; color: TaosStyle.textSecondary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 130
            }
            Txt { text: Fmt.relative(row.ts, list.nowSec); font.pixelSize: 12; color: TaosStyle.textTertiary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 64 }
        }
    }
}
