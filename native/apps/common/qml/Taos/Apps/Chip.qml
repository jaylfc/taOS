import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// A small pill: a label, an optional glyph, a tone.
Rectangle {
    id: root
    property string text: ""
    property string tone: "neutral"       // neutral | accent | success | warning | danger
    property string glyph: ""
    property bool mono: false
    property string accessibleName: text
    implicitHeight: 20
    implicitWidth: row.implicitWidth + 14
    radius: height / 2
    color: tone === "neutral" ? Look.field : Look.tint(Look.tone(tone), 0.14)
    border.width: 1
    border.color: tone === "neutral" ? Look.hairline : Look.tint(Look.tone(tone), 0.30)
    Accessible.role: Accessible.StaticText
    Accessible.name: accessibleName
    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 4
        Glyph { visible: root.glyph !== ""; name: root.glyph; size: 11; stroke: 2
                color: root.tone === "neutral" ? Look.textSecondary : Look.tone(root.tone) }
        TaosText {
            text: root.text
            font.pixelSize: Look.caption
            font.weight: Font.Medium
            font.family: root.mono ? "monospace" : Look.family
            color: root.tone === "neutral" ? Look.textSecondary : Look.tone(root.tone)
        }
    }
}
