import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One agent in the list: an initial on a surface, name, what it runs, where it
// runs, and its state as a word. Selection and focus share the accent.
C.ItemDelegate {
    id: row
    property bool selected: false
    property string agentName: ""
    property string title: ""
    property string subtitle: ""
    property string agentState: ""
    property string busyLabel: ""
    property string deviceName: ""
    property string deviceKind: ""
    property string deviceReach: ""
    property string ownerLabel: ""
    property string errorText: ""

    height: 74
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("%1, %2, runs on %3, %4%5").arg(title).arg(busyLabel !== "" ? busyLabel : Tone.stateLabel(agentState))
        .arg(deviceName).arg(Tone.reachLabel(deviceReach)).arg(errorText !== "" ? qsTr(", last action failed") : "")
    Accessible.selected: selected

    background: Rectangle {
        color: row.selected ? Look.selection : row.hovered ? Look.hover : "transparent"
        Behavior on color { ColorAnimation { duration: Look.base } }
        Rectangle { visible: row.visualFocus; anchors.fill: parent; anchors.margins: 2; radius: Look.radiusControl
                    color: "transparent"; border.width: 2; border.color: Look.focusRing }
        Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                    anchors.leftMargin: 16; height: 1; color: Look.hairline }
    }
    contentItem: RowLayout {
        spacing: 12
        Rectangle {
            Layout.leftMargin: 4
            Layout.alignment: Qt.AlignVCenter
            width: 36; height: 36; radius: 18
            color: Look.raised
            border.width: 1; border.color: Look.hairline
            TaosText { anchors.centerIn: parent; text: Tone.initial(row.title); font.pixelSize: Look.bodyLarge
                       font.weight: Font.DemiBold; color: Look.textSecondary }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 3
            TaosText { Layout.fillWidth: true; text: row.title; elide: Text.ElideRight
                       font.pixelSize: Look.bodyLarge; font.weight: Font.Medium }
            TaosText { Layout.fillWidth: true; text: row.subtitle; elide: Text.ElideRight
                       font.pixelSize: Look.small; color: Look.textSecondary }
            DeviceTag { deviceName: row.deviceName; kind: row.deviceKind; reach: row.deviceReach }
        }
        ColumnLayout {
            Layout.alignment: Qt.AlignVCenter
            Layout.rightMargin: 8
            spacing: 3
            TaosText {
                Layout.alignment: Qt.AlignRight
                text: row.busyLabel !== "" ? row.busyLabel + "..." : Tone.stateLabel(row.agentState)
                font.pixelSize: Look.small; font.weight: Font.DemiBold
                color: row.busyLabel !== "" ? Look.textSecondary : Tone.stateColor(row.agentState)
            }
            TaosText {
                Layout.alignment: Qt.AlignRight
                visible: row.errorText !== ""
                text: qsTr("Action failed")
                font.pixelSize: Look.caption; color: Look.danger
            }
            TaosText {
                Layout.alignment: Qt.AlignRight
                visible: row.ownerLabel !== "" && row.errorText === ""
                text: row.ownerLabel; font.pixelSize: Look.caption; color: Look.textTertiary
                elide: Text.ElideRight; Layout.maximumWidth: 110
            }
        }
    }
}
