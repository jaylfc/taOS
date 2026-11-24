import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import "qrc:/taos/qml/Taos/Apps/Logic.js" as Logic

// A pending decision an agent is waiting on. Answering and noting POST exactly what
// DecisionsApp.tsx POSTs (the answer adds source "native", which the controller
// records; answered_by is left empty so the server records the session user).
// Typed-but-unsent text is the only state held here, and it is lost on close by design.
GlassPanel {
    id: root
    required property var model
    property bool busy: false
    property string errorText: ""
    property string selectedSingle: ""
    property var picked: []
    property bool otherSelected: false
    signal answered(string id)
    signal changed()

    readonly property string type: model.type || "free_text"
    readonly property var options: Logic.arr(model.options)
    readonly property var notes: Logic.arr(model.notes)
    readonly property var gate: Logic.gateDetails(model.metadata)
    readonly property string agentHandle: (model.from_agent || "").charAt(0) === "@" ? model.from_agent : "@" + model.from_agent
    readonly property bool isOtherPicked: type === "single_select" ? selectedSingle === "__other__" : otherSelected
    readonly property bool canSubmit: !busy && (
        type === "single_select" ? selectedSingle !== "" && (selectedSingle !== "__other__" || otherArea.text.trim() !== "")
        : type === "multi_select" ? picked.length > 0 || (otherSelected && otherArea.text.trim() !== "")
        : type === "free_text" ? freeArea.text.trim() !== "" : true)

    implicitHeight: col.implicitHeight + 32
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Decision from %1: %2").arg(agentHandle).arg(model.question)

    function focusFirst() {
        if (optionRepeater.count > 0 && optionRepeater.itemAt(0)) optionRepeater.itemAt(0).forceActiveFocus(Qt.TabFocusReason)
        else if (type === "free_text") freeArea.forceFocus()
        else if (approveButton.visible) approveButton.forceActiveFocus(Qt.TabFocusReason)
        else noteField.forceActiveFocus(Qt.TabFocusReason)
    }

    function togglePicked(v) {
        var cur = picked.slice()
        var at = cur.indexOf(v)
        if (at >= 0) cur.splice(at, 1); else cur.push(v)
        picked = cur
    }

    // POST /api/decisions/{id}/answer  (DecisionsApp.tsx:860-880)
    function submit(value) {
        errorText = ""
        busy = true
        var body = { value: value, source: "native" }
        var other = otherArea.text.trim()
        if (other !== "") body.other_value = other
        var note = answerNote.text.trim()
        if (note !== "") body.note = note
        Taos.http("POST", "/api/decisions/" + encodeURIComponent(model.id) + "/answer", {}, body, function (ok, v, err) {
            if (!root) return
            root.busy = false
            if (!ok) {
                root.errorText = err || qsTr("Could not record answer.")
                root.changed()          // another answer may have won: refetch (409)
                return
            }
            root.answered(root.model.id)
        })
    }

    // POST /api/decisions/{id}/note  (DecisionsApp.tsx:882-899)
    function submitNote() {
        var text = noteField.text.trim()
        if (text === "" || noteBusy) return
        errorText = ""
        noteBusy = true
        Taos.http("POST", "/api/decisions/" + encodeURIComponent(model.id) + "/note", {}, { text: text }, function (ok, v, err) {
            if (!root) return
            root.noteBusy = false
            if (!ok) { root.errorText = err || qsTr("Could not add note."); return }
            noteField.text = ""
            root.changed()
        })
    }
    property bool noteBusy: false

    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
        spacing: 12

        // header
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Chip { text: root.agentHandle; tone: "accent" }
            Chip { visible: root.model.priority === "blocking"; text: qsTr("Blocking"); tone: "warning"; glyph: "alert" }
            Chip { visible: !!root.model.project_id; text: root.model.project_id || ""; tone: "neutral" }
            Item { Layout.fillWidth: true }
            Glyph { name: "clock"; size: 12; color: Look.textTertiary }
            TaosText { text: Format.relativeTime(root.model.created_at, Format.tick); font.pixelSize: Look.small; color: Look.textTertiary }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            TaosText {
                Layout.fillWidth: true
                text: root.model.question
                wrapMode: Text.Wrap
                font.pixelSize: Look.title
                font.weight: Font.DemiBold
            }
            // What approving grants (gate decisions): grant-bearing metadata, before the user answers.
            Rectangle {
                visible: root.gate !== null
                Layout.fillWidth: true
                implicitHeight: gateCol.implicitHeight + 20
                radius: 10
                color: Look.tint(Look.warning, 0.07)
                border.width: 1
                border.color: Look.tint(Look.warning, 0.4)
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("What approving grants")
                ColumnLayout {
                    id: gateCol
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
                    spacing: 4
                    TaosText { text: qsTr("Approving: %1").arg(root.gate ? root.gate.title : ""); font.weight: Font.DemiBold
                               font.pixelSize: Look.small; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    GridLayout {
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 2
                        Layout.fillWidth: true
                        Repeater {
                            model: root.gate ? root.gate.rows.length * 2 : 0
                            TaosText {
                                required property int index
                                readonly property var row: root.gate.rows[Math.floor(index / 2)]
                                Layout.fillWidth: index % 2 === 1
                                Layout.alignment: Qt.AlignTop
                                text: index % 2 === 0 ? row.label : row.value
                                font.pixelSize: Look.small
                                font.family: index % 2 === 1 ? "monospace" : Look.family
                                color: index % 2 === 0 ? Look.textTertiary : Look.textSecondary
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                    }
                }
            }
            TaosText {
                visible: !!root.model.context
                Layout.fillWidth: true
                text: root.model.context || ""
                wrapMode: Text.Wrap
                color: Look.textSecondary
                font.pixelSize: Look.body
                lineHeight: 1.2
            }
            // notes
            Repeater {
                model: root.notes
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: noteCol.implicitHeight
                    color: "transparent"
                    Rectangle { width: 2; height: parent.height; color: Look.hairline; radius: 1 }
                    ColumnLayout {
                        id: noteCol
                        anchors { left: parent.left; right: parent.right; leftMargin: 12 }
                        spacing: 1
                        TaosText { text: modelData.text; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Look.textSecondary
                                   font.pixelSize: Look.small }
                        TaosText { text: modelData.author + " · " + Format.relativeTime(modelData.created_at, Format.tick)
                                   font.pixelSize: Look.caption; color: Look.textTertiary }
                    }
                }
            }
            TaosText {
                text: ({ single_select: qsTr("PICK ONE"), multi_select: qsTr("PICK ANY"), approve_deny: qsTr("APPROVE / DENY"),
                         free_text: qsTr("FREE TEXT") })[root.type] || ""
                font.pixelSize: Look.caption - 1
                font.letterSpacing: 0.8
                font.weight: Font.DemiBold
                color: Look.textTertiary
            }
        }

        // options (single and multi)
        ColumnLayout {
            visible: root.type === "single_select" || root.type === "multi_select"
            Layout.fillWidth: true
            spacing: 6
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("Answer options")
            Repeater {
                id: optionRepeater
                model: root.options
                delegate: OptionRow {
                    required property var modelData
                    Layout.fillWidth: true
                    label: modelData.label
                    rationale: modelData.rationale || ""
                    recommended: modelData.recommended === true
                    multi: root.type === "multi_select"
                    busy: root.busy
                    selected: root.type === "single_select" ? root.selectedSingle === modelData.value
                                                            : root.picked.indexOf(modelData.value) >= 0
                    onToggled: root.type === "single_select" ? root.selectedSingle = modelData.value : root.togglePicked(modelData.value)
                }
            }
            OptionRow {
                Layout.fillWidth: true
                label: qsTr("Other")
                multi: root.type === "multi_select"
                busy: root.busy
                selected: root.isOtherPicked
                onToggled: root.type === "single_select" ? root.selectedSingle = "__other__" : root.otherSelected = !root.otherSelected
            }
            TaosArea {
                id: otherArea
                visible: root.isOtherPicked
                Layout.fillWidth: true
                rows: 2
                maximumLength: 20000
                placeholderText: qsTr("Type your answer...")
                accessibleName: qsTr("Other answer")
            }
            TaosField {
                id: answerNote
                Layout.fillWidth: true
                maximumLength: 500
                placeholderText: qsTr("Add a note (optional)")
                accessibleName: qsTr("Answer note")
            }
            RowLayout {
                visible: root.type === "single_select" ? root.selectedSingle !== "" : true
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TaosButton {
                    text: root.busy ? qsTr("Saving...") : qsTr("Submit")
                    variant: "primary"
                    enabled: root.canSubmit
                    onClicked: root.submit(root.type === "single_select"
                                           ? (root.selectedSingle === "__other__" ? "" : root.selectedSingle) : root.picked)
                }
            }
        }

        // approve / deny
        RowLayout {
            visible: root.type === "approve_deny"
            Layout.fillWidth: true
            spacing: 8
            TaosButton {
                id: approveButton
                text: qsTr("Approve"); glyph: "check"; variant: "primary"
                enabled: !root.busy
                onClicked: root.submit("approve")
            }
            TaosButton {
                text: qsTr("Deny"); glyph: "x"; variant: "danger"
                enabled: !root.busy
                onClicked: root.submit("deny")
            }
        }

        // free text
        ColumnLayout {
            visible: root.type === "free_text"
            Layout.fillWidth: true
            spacing: 8
            TaosArea {
                id: freeArea
                Layout.fillWidth: true
                rows: 3
                maximumLength: 20000
                placeholderText: qsTr("Type your answer...")
                accessibleName: qsTr("Answer")
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TaosButton {
                    text: root.busy ? qsTr("Saving...") : qsTr("Submit")
                    variant: "primary"
                    enabled: root.canSubmit
                    onClicked: root.submit(freeArea.text.trim())
                }
            }
        }

        TaosText {
            visible: root.errorText !== ""
            Layout.fillWidth: true
            text: root.errorText
            wrapMode: Text.Wrap
            color: Look.danger
            font.pixelSize: Look.small
            Accessible.ignored: false
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
        }

        // note composer
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            TaosField {
                id: noteField
                Layout.fillWidth: true
                maximumLength: 500
                placeholderText: qsTr("Add a note...")
                accessibleName: qsTr("Note text")
                onAccepted: root.submitNote()
            }
            TaosButton {
                text: root.noteBusy ? qsTr("Adding...") : qsTr("Add note")
                enabled: !root.noteBusy && noteField.text.trim() !== ""
                onClicked: root.submitNote()
            }
        }
    }
}
