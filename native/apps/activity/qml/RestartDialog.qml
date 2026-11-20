import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// The recovery action's dialog: confirm, running, done (per-unit results) and
// error. The server returns the outcome; this only shows it.
Popup {
    id: dlg
    property var eng
    readonly property int phase: eng ? eng.restartPhase : 0
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(460, (parent ? parent.width : 460) - 32)
    padding: 22
    closePolicy: phase === ActivityEngine.RestartRunning ? Popup.NoAutoClose : Popup.CloseOnEscape
    onPhaseChanged: { if (phase !== ActivityEngine.RestartIdle) open(); else close() }
    onClosed: if (eng && eng.restartPhase !== ActivityEngine.RestartIdle) eng.dismissRestart()
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.dur(160) } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Pal.dur(120) } }
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.5) }
    background: Rectangle {
        radius: 16
        color: Qt.tint(TaosStyle.ground, Pal.alpha(TaosStyle.text, 0.07))
        border.width: 1
        border.color: Pal.hairline
    }
    function unitLabel(u) { return u.replace(/\.service$/, "") }

    contentItem: ColumnLayout {
        spacing: 14
        Accessible.role: Accessible.Dialog
        Accessible.name: qsTr("Restart AI services")
        // confirm
        ColumnLayout {
            visible: dlg.phase === ActivityEngine.RestartConfirm
            spacing: 12
            Layout.fillWidth: true
            Txt { text: qsTr("Restart AI services?"); font.pixelSize: 17; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
            Txt {
                Layout.fillWidth: true
                text: dlg.eng ? dlg.eng.restartSummary() : ""
                wrapMode: Text.WordWrap; elide: Text.ElideNone
                color: TaosStyle.textSecondary
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8
                GlassButton { text: qsTr("Cancel"); onClicked: dlg.eng.dismissRestart() }
                GlassButton { text: qsTr("Restart"); prominent: true; danger: true; focus: true; onClicked: dlg.eng.confirmRestart() }
            }
        }
        // running
        RowLayout {
            visible: dlg.phase === ActivityEngine.RestartRunning
            spacing: 12
            BusyIndicator { running: parent.visible; implicitWidth: 28; implicitHeight: 28 }
            Txt { text: qsTr("Restarting AI services…"); color: TaosStyle.textSecondary }
            Accessible.role: Accessible.StaticText
            Accessible.name: qsTr("Restarting AI services")
        }
        // done
        ColumnLayout {
            id: done
            visible: dlg.phase === ActivityEngine.RestartDone
            spacing: 12
            Layout.fillWidth: true
            readonly property var res: dlg.eng ? dlg.eng.restartResult : ({})
            readonly property var results: res.results || []
            readonly property var failed: res.failed || []
            readonly property var restarted: res.restarted || []
            Txt {
                font.pixelSize: 17; font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
                text: done.res.status === "noop" ? qsTr("Nothing to restart")
                    : done.failed.length === 0 ? qsTr("AI services restarted")
                    : done.restarted.length > 0 ? qsTr("Some AI services restarted") : qsTr("Could not restart AI services")
                color: done.failed.length === 0 ? Pal.ok : Pal.warn
            }
            Txt {
                visible: done.res.status === "noop"
                Layout.fillWidth: true
                wrapMode: Text.WordWrap; elide: Text.ElideNone
                color: TaosStyle.textSecondary
                text: done.res.detail || qsTr("No managed AI backends are installed on this node.")
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                Accessible.role: Accessible.List
                Accessible.name: qsTr("Restart results")
                Repeater {
                    model: done.results
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 10
                        Accessible.role: Accessible.ListItem
                        Accessible.name: dlg.unitLabel(modelData.unit) + ", " + (modelData.ok ? qsTr("restarted") : (modelData.detail || qsTr("failed")))
                        Txt { text: modelData.ok ? "✓" : "✗"; color: modelData.ok ? Pal.ok : Pal.crit; font.weight: Font.Bold }
                        Txt { text: dlg.unitLabel(modelData.unit); Layout.fillWidth: true; color: TaosStyle.textSecondary }
                        Txt {
                            text: modelData.ok ? qsTr("restarted") : (modelData.detail || qsTr("failed"))
                            font.pixelSize: 11; color: modelData.ok ? Pal.ok : Pal.warn
                            Layout.maximumWidth: 220
                        }
                    }
                }
            }
            Txt {
                visible: done.failed.length > 0
                Layout.fillWidth: true
                wrapMode: Text.WordWrap; elide: Text.ElideNone
                font.pixelSize: 12; color: TaosStyle.textTertiary
                text: qsTr("Some services could not be restarted automatically. They may need permissions this device has not granted yet.")
            }
            GlassButton { text: qsTr("Close"); Layout.alignment: Qt.AlignRight; focus: true; onClicked: dlg.eng.dismissRestart() }
        }
        // error
        ColumnLayout {
            visible: dlg.phase === ActivityEngine.RestartError
            spacing: 12
            Layout.fillWidth: true
            Txt { text: qsTr("Restart failed"); font.pixelSize: 17; font.weight: Font.DemiBold; color: Pal.crit; Accessible.role: Accessible.Heading }
            Txt {
                Layout.fillWidth: true
                text: dlg.eng ? dlg.eng.restartError : ""
                wrapMode: Text.WordWrap; elide: Text.ElideNone
                color: TaosStyle.textSecondary
                Accessible.role: Accessible.AlertMessage
            }
            GlassButton { text: qsTr("Close"); Layout.alignment: Qt.AlignRight; focus: true; onClicked: dlg.eng.dismissRestart() }
        }
    }
}
