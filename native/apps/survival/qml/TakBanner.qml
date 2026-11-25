// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// What the mesh engine is doing, said once at the top of taOStak: not running (with what to do), connected, and
// whether sending is possible. Honest in every state; never hides that nothing can be sent.
ColumnLayout {
    id: root
    readonly property var tak: Survival.tak
    spacing: Look.space2
    GlassPanel {
        visible: !root.tak.available
        objectName: "engineDown"
        Layout.fillWidth: true
        implicitHeight: downCol.implicitHeight + Look.space3 * 2
        ColumnLayout {
            id: downCol
            anchors { fill: parent; margins: Look.space3 }
            spacing: Look.space2
            RowLayout {
                spacing: Look.space2
                Glyph { name: root.tak.state === "failed" ? "alert" : "radio"; size: 18; stroke: 1.8; color: root.tak.state === "failed" ? Look.warning : Look.textSecondary }
                TaosText { text: root.tak.state === "connecting" ? qsTr("Connecting to the mesh engine") : root.tak.state === "failed" ? qsTr("The mesh engine has a problem") : qsTr("The mesh engine is not running")
                           font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap }
            }
            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: root.tak.state === "failed" ? root.tak.reason : qsTr("Radios, messages and people appear here once it runs. Nothing is sent while it is off.") }
            RowLayout {
                spacing: Look.space2
                PillButton { visible: root.tak.canStartEngine; compact: true; variant: "primary"; text: qsTr("Start the engine"); glyph: "play"; onClicked: root.tak.startEngine() }
                PillButton { compact: true; text: qsTr("Check again"); glyph: "refresh"; onClicked: root.tak.refresh() }
            }
        }
    }
    Flow {
        visible: root.tak.available
        Layout.fillWidth: true
        spacing: Look.space2
        Chip { text: qsTr("Engine connected"); tone: "success"; glyph: "check" }
        Chip { visible: root.tak.receiveOnly; text: qsTr("Receive only"); tone: "warning"; glyph: "shield" }
        Chip { visible: !root.tak.receiveOnly; text: root.tak.sendingOn ? qsTr("Sending on") : qsTr("Sending off"); tone: root.tak.sendingOn ? "accent" : "neutral" }
    }
    TaosText {
        visible: root.tak.notice !== ""
        objectName: "takNotice"
        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small
        color: root.tak.noticeIsError ? Look.danger : Look.success
        text: root.tak.notice
        Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
    }
}
