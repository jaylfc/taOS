import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// Where an agent runs: a kind icon, the device name and whether it is reachable.
// Offline is spelled out; the icon and colour only support the words.
RowLayout {
    id: root
    property string deviceName: ""
    property string kind: ""
    property string reach: ""
    property bool showReach: true
    spacing: 5
    Accessible.role: Accessible.StaticText
    Accessible.name: qsTr("Runs on %1, %2").arg(root.deviceName).arg(Tone.reachLabel(root.reach))
    AgentIcon { name: Tone.deviceIcon(root.kind); size: 13; color: Tone.reachColor(root.reach) }
    TaosText { text: root.deviceName; font.pixelSize: Look.small; color: Look.textSecondary
               elide: Text.ElideRight; Layout.maximumWidth: 140 }
    TaosText {
        visible: root.showReach && root.reach !== "online"
        text: Tone.reachLabel(root.reach)
        font.pixelSize: Look.small
        font.weight: Font.Medium
        color: Tone.reachColor(root.reach)
    }
}
