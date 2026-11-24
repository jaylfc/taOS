// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import "qrc:/taos/qml/Taos/Apps/Logic.js" as Logic

// An answered or withdrawn decision (DecisionsApp.tsx:604-655), with the lazy
// supersession history (514-602).
GlassPanel {
    id: root
    required property var model
    property bool historyOpen: false
    property bool historyLoading: false
    property string historyError: ""
    property var chain: null
    property int historyToken: 0

    readonly property var notes: Logic.arr(model.notes)
    readonly property string agentHandle: (model.from_agent || "").charAt(0) === "@" ? model.from_agent : "@" + model.from_agent
    readonly property var stamp: model.answered_at || (model.answer && model.answer.answered_at) || model.withdrawn_at || model.created_at

    implicitHeight: col.implicitHeight + 28
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("%1: %2").arg(agentHandle).arg(model.question)
    Component.onDestruction: historyToken++

    // GET /api/decisions/{id}/history, fetched on first expand
    function toggleHistory() {
        if (historyOpen) { historyOpen = false; return }
        historyOpen = true
        if (chain !== null || historyLoading) return
        historyLoading = true
        historyError = ""
        const tok = ++historyToken
        Taos.call("decisions.history", { id: model.id }, function (ok, value, err) {
            if (!root || tok !== root.historyToken) return
            root.historyLoading = false
            if (!ok) { root.historyError = err || qsTr("Could not load history."); return }
            root.chain = Logic.arr(value && value.items !== undefined ? value.items : value)
        })
    }

    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Chip { text: root.agentHandle; tone: "accent" }
            Chip { visible: !!root.model.project_id; text: root.model.project_id || "" }
            Item { Layout.fillWidth: true }
            TaosText { text: Format.relativeTime(root.stamp, Format.tick); font.pixelSize: Look.small; color: Look.textTertiary }
        }
        TaosText { Layout.fillWidth: true; text: root.model.question; wrapMode: Text.Wrap
                   font.pixelSize: Look.bodyLarge; font.weight: Font.DemiBold }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Glyph { name: root.model.status === "withdrawn" ? "x" : "check"; size: 15; stroke: 2
                    color: root.model.status === "withdrawn" ? Look.textTertiary : Look.success
                    Layout.alignment: Qt.AlignTop }
            TaosText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Look.textSecondary
                text: root.model.status === "withdrawn"
                      ? qsTr("Withdrawn by %1%2").arg(root.model.withdrawn_by || root.model.from_agent)
                            .arg(root.model.withdraw_reason ? ": " + root.model.withdraw_reason : "")
                      : root.model.status === "superseded" ? qsTr("Superseded") : Logic.answerLabel(root.model)
            }
        }
        Repeater {
            model: root.notes
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: nCol.implicitHeight
                color: "transparent"
                Rectangle { width: 2; height: parent.height; color: Look.hairline; radius: 1 }
                ColumnLayout {
                    id: nCol
                    anchors { left: parent.left; right: parent.right; leftMargin: 12 }
                    spacing: 1
                    TaosText { text: modelData.text; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Look.textSecondary
                               font.pixelSize: Look.small }
                    TaosText { text: modelData.author + " · " + Format.relativeTime(modelData.created_at, Format.tick)
                               font.pixelSize: Look.caption; color: Look.textTertiary }
                }
            }
        }
        // history
        TaosButton {
            visible: !!root.model.parent_decision_id
            compact: true
            variant: "ghost"
            glyph: root.historyOpen ? "chevron-down" : "history"
            text: root.historyOpen ? qsTr("Hide history") : qsTr("View history")
            Accessible.checkable: true
            Accessible.checked: root.historyOpen
            onClicked: root.toggleHistory()
        }
        ColumnLayout {
            visible: root.historyOpen
            Layout.fillWidth: true
            Layout.leftMargin: 6
            spacing: 6
            TaosText { visible: root.historyLoading; text: qsTr("Loading history..."); font.pixelSize: Look.small; color: Look.textTertiary }
            TaosText {
                visible: root.historyError !== ""
                text: root.historyError; font.pixelSize: Look.small; color: Look.danger
                Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text
            }
            Repeater {
                model: root.chain || []
                delegate: ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 1
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; color: Look.textSecondary; font.pixelSize: Look.small
                               text: (index + 1) + ". " + modelData.question }
                    TaosText {
                        Layout.leftMargin: 16
                        font.pixelSize: Look.caption
                        color: Look.textTertiary
                        text: (modelData.status === "superseded" ? qsTr("superseded") : modelData.status === "withdrawn" ? qsTr("withdrawn")
                               : Logic.answerLabel(modelData)) + " · "
                              + Format.relativeTime((modelData.answer && modelData.answer.answered_at) || modelData.created_at, Format.tick)
                    }
                }
            }
            TaosText { visible: root.chain !== null && root.chain.length === 0 && !root.historyLoading
                       text: qsTr("No earlier versions."); font.pixelSize: Look.small; color: Look.textTertiary }
        }
    }
}
