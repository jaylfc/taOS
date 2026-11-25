// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// One conversation. Each message shows who is behind it, when, and where they were and how strong the signal was, from
// the engine's own record. Sending is only enabled when the engine is not receive-only and the owner has turned
// sending on; otherwise the box says why it is off.
ColumnLayout {
    id: root
    property var nav
    property string threadId: ""
    readonly property var tak: Survival.tak
    readonly property var msgs: { tak.threads; return tak.messages(threadId) }
    readonly property string blocked: { tak.threads; return tak.sendBlocked }
    spacing: 0
    Component.onCompleted: tak.markRead(threadId)
    onMsgsChanged: Qt.callLater(() => tak.markRead(threadId))
    C.ScrollView {
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: Look.space3
            width: parent.width - Look.space3 * 2
            spacing: Look.space3
            Item { Layout.preferredHeight: Look.space1 }
            Repeater {
                model: root.msgs
                delegate: ColumnLayout {
                    id: bub
                    required property var modelData
                    objectName: "msg-" + modelData.id
                    Layout.fillWidth: true
                    spacing: 2
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Look.space2
                        TaosText { text: bub.modelData.who; font.pixelSize: Look.small; font.weight: Font.DemiBold
                                   color: bub.modelData.outgoing ? Look.accent : Look.text }
                        Chip { visible: bub.modelData.whoKind === "agent"; text: qsTr("Agent"); glyph: "bot" }
                        Chip { visible: bub.modelData.trust === "claimed"; text: qsTr("Unverified sender") }
                        Item { Layout.fillWidth: true }
                        TaosText { text: bub.modelData.ago; font.pixelSize: Look.caption; color: Look.textSecondary }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: txt.implicitHeight + Look.space3 * 2
                        radius: Look.radiusCard - 4
                        color: bub.modelData.outgoing ? Look.tint(Look.accent, 0.14) : Look.field
                        border.width: 1
                        border.color: bub.modelData.outgoing ? Look.tint(Look.accent, 0.30) : Look.hairline
                        TaosText { id: txt; anchors { fill: parent; margins: Look.space3 } text: bub.modelData.text; wrapMode: Text.Wrap; font.pixelSize: Look.bodyMedium }
                    }
                    TaosText { visible: bub.modelData.positionText !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textSecondary
                               text: qsTr("Position %1").arg(bub.modelData.positionText) }
                    TaosText { visible: bub.modelData.signalText !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textSecondary
                               text: bub.modelData.signalText }
                    TaosText { visible: bub.modelData.outgoing && bub.modelData.delivery !== ""; font.pixelSize: Look.caption
                               color: bub.modelData.delivery === "failed" ? Look.danger : Look.textSecondary
                               text: bub.modelData.delivery === "queued" ? qsTr("Queued") : bub.modelData.delivery === "sent" ? qsTr("Sent to the radio") : bub.modelData.delivery === "heard" ? qsTr("Heard by a relay")
                                     : bub.modelData.delivery === "acknowledged" ? qsTr("Delivered") : bub.modelData.delivery === "failed" ? qsTr("Failed") : bub.modelData.delivery }
                }
            }
            EmptyState {
                visible: root.msgs.length === 0
                Layout.fillWidth: true; Layout.topMargin: Look.space6
                glyph: "message"; title: qsTr("Nothing here yet"); detail: qsTr("Messages in this conversation appear as they are heard.")
            }
            Item { Layout.preferredHeight: Look.space2 }
        }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
    ColumnLayout {
        Layout.fillWidth: true; Layout.margins: Look.space3
        spacing: Look.space2
        RowLayout {
            visible: root.blocked !== ""
            objectName: "sendOff"
            Layout.fillWidth: true; spacing: Look.space2
            Glyph { name: "shield"; size: 14; stroke: 2; color: Look.textSecondary }
            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary; text: root.blocked }
        }
        PillButton {
            visible: root.tak.available && !root.tak.receiveOnly && !root.tak.sendingOn
            objectName: "sendingOnButton"
            compact: true; text: qsTr("Turn sending on"); glyph: "send"
            onClicked: root.tak.sendingOn = true
        }
        RowLayout {
            Layout.fillWidth: true; spacing: Look.space2
            TaosField {
                id: msgField
                objectName: "composeField"
                Layout.fillWidth: true
                enabled: root.blocked === ""
                placeholderText: root.blocked === "" ? qsTr("Message") : qsTr("Sending is off")
                accessibleName: qsTr("Message")
                onAccepted: sendBtn.clicked()
            }
            TaosButton {
                id: sendBtn
                objectName: "sendButton"
                glyph: "send"; variant: "primary"
                enabled: root.blocked === "" && msgField.text.trim() !== ""
                accessibleName: qsTr("Send")
                onClicked: { const e = root.tak.send(root.threadId, msgField.text); if (e === "") msgField.text = "" }
            }
        }
    }
}
