// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Switch one radio from its network to another. Four stages: choose the network, read what will change, type the board's
// name to confirm, then watch it happen (settings saved, port released, firmware written, reconnected in the new mode,
// earlier settings put back). A failure says which stage stopped and how to get the board back. The flash itself is done
// by the firmware manager, which asks for the typed name and the owner's one-use permission again.
C.ScrollView {
    id: root
    property var nav
    property string rowKey: ""
    readonly property var tak: Survival.tak
    readonly property var fw: Survival.firmware
    readonly property var sw: Survival.switcher
    property string entryId: ""
    property string chosen: "choose"          // choose | review
    readonly property string phase: sw.step === "idle" ? chosen : (sw.step === "done" || sw.step === "failed" ? "result" : "run")
    readonly property var liveRow: { tak.radioRows; return tak.radioRow(rowKey) }
    readonly property var row: sw.step !== "idle" ? sw.subject : liveRow
    readonly property string boardName: row.hardware !== "" && row.hardware !== undefined ? row.hardware : (fw.plan.board ? fw.boardName(fw.plan.board) : "")
    readonly property var entry: { for (const e of fw.library) if (e.id === entryId) return e; return null }
    readonly property string toNetwork: entry ? entry.network : ""
    readonly property bool hasEarlier: row.profiles ? row.profiles.some(p => p.network === toNetwork) : false
    contentWidth: availableWidth
    clip: true
    Component.onCompleted: { fw.reload(); fw.detect(); tak.discover() }
    function networkName(n) { return n === "meshtastic" ? "Meshtastic" : n === "meshcore" ? "MeshCore" : n === "rnode" ? "RNode (Reticulum)" : n }
    // one choice per network and variant: the newest stored version that fits this board, other than what it runs now
    readonly property var choices: {
        const best = {}
        const cands = row.candidates || []
        for (const e of fw.library) {
            if (e.network === "rnode" || e.network === row.network) continue
            if (cands.length > 0 && cands.indexOf(e.board) < 0) continue
            const k = e.network + "|" + e.board + "|" + e.variant
            if (!best[k]) best[k] = e
        }
        return Object.keys(best).sort().map(k => best[k])
    }
    // scripted states for pictures (only with TAOS_SURVIVAL_DEMO): --demo swstep=review;entry=<id>;typed=<text>
    onNavChanged: if (nav && nav.demo.swstep !== undefined) demoTimer.start()
    Timer {
        id: demoTimer
        interval: 300; repeat: true
        onTriggered: {
            if (!root.nav || root.row.key === undefined) return
            if (root.nav.demo.swstep === "choose") { stop(); return }
            const want = root.nav.demo.entry !== undefined ? root.nav.demo.entry : (root.choices.length > 0 ? root.choices[0].id : "")
            if (want === "" ) return
            if (root.entryId === "") { root.entryId = want; root.fw.planFlash(root.row.fwDeviceId, want); root.chosen = "review"; return }
            // swgo=run starts the switch with the board name typed right, swgo=wrong with a wrong one (the manager refuses it)
            if (root.nav.demo.swgo !== undefined && root.fw.plan.board && root.sw.step === "idle")
                root.sw.begin(root.rowKey, root.entryId, root.nav.demo.swgo === "wrong" ? qsTr("Wrong name") : root.fw.boardName(root.fw.plan.board), true)
            stop()
        }
    }
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space3

        // ---------------------------------------------------------------- the radio being switched
        RowLayout {
            Layout.topMargin: Look.space3; spacing: Look.space2
            Chip { text: root.row.networkName ? root.row.networkName : qsTr("Radio") }
            TaosText { text: root.row.title ? root.row.title : ""; font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
        }
        EmptyState {
            visible: !root.row.key
            Layout.fillWidth: true
            glyph: "usb"; title: qsTr("That radio is not listed any more")
            detail: qsTr("It may have been unplugged. Go back to the Radios page and plug it in again.")
        }

        // ---------------------------------------------------------------- choose the network
        ColumnLayout {
            visible: root.phase === "choose" && !!root.row.key
            Layout.fillWidth: true; spacing: Look.space2
            TaosText { text: qsTr("Switch to"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Only firmware stored for this board is offered, from the firmware library.") }
            Repeater {
                model: root.choices
                delegate: ListRow {
                    required property var modelData
                    Layout.fillWidth: true
                    objectName: "choice-" + modelData.id
                    glyph: "radio"
                    title: root.networkName(modelData.network) + (modelData.variant !== "" ? " (" + modelData.variant.toUpperCase() + ")" : "")
                    subtitle: qsTr("Version %1, %2").arg(modelData.version).arg(root.fw.sizeText(modelData.sizeBytes))
                    onClicked: { root.entryId = modelData.id; root.fw.planFlash(root.row.fwDeviceId, modelData.id); root.chosen = "review" }
                    trailing: Component { Glyph { name: "chevron-right"; size: 16; stroke: 2; color: Look.textTertiary } }
                }
            }
            ListRow {
                objectName: "choice-reticulum"
                Layout.fillWidth: true
                glyph: "clock"; clickable: false; enabled: false
                title: qsTr("Reticulum (RNode)")
                subtitle: qsTr("Coming: no Reticulum driver yet")
            }
            TaosText { visible: root.choices.length === 0; objectName: "nothingStored"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Nothing stored fits this board yet. Open the firmware library and refresh it first.") }
            PillButton { compact: true; text: qsTr("Back"); variant: "outline"; onClicked: root.nav.back() }
        }

        // ---------------------------------------------------------------- what will change, and the confirmation
        ColumnLayout {
            visible: root.phase === "review"
            Layout.fillWidth: true; spacing: Look.space3
            TaosText { text: qsTr("What will change"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
            TaosText { visible: root.fw.planError !== ""; objectName: "planError"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.danger; text: root.fw.planError }
            TaosText { visible: root.fw.planError === "" && !root.fw.plan.board; Layout.fillWidth: true; font.pixelSize: Look.body; color: Look.textSecondary; text: qsTr("Checking the board...") }
            ColumnLayout {
                visible: !!root.fw.plan.board
                Layout.fillWidth: true; spacing: Look.space2
                Repeater {
                    objectName: "changes"
                    model: [
                        qsTr("This radio stops being a %1 radio and becomes a %2 radio (%3). People on %1 will no longer hear it.").arg(root.row.networkName || qsTr("current-network")).arg(root.networkName(root.toNetwork)).arg(root.entry ? root.entry.version : ""),
                        root.row.network !== "" && root.row.engineRadioId !== ""
                            ? qsTr("Its %1 settings (names, region, channels and their keys) are saved on this device first, in a private file. Switching back puts them back.").arg(root.row.networkName)
                            : qsTr("The mesh engine has no open connection to this board, so its settings cannot be read. The firmware manager still backs up what it can."),
                        root.hasEarlier ? qsTr("Settings you saved earlier for %1 are put back once the radio is up.").arg(root.networkName(root.toNetwork))
                                        : qsTr("No settings were saved for %1 before, so it starts with the new firmware's own defaults.").arg(root.networkName(root.toNetwork)),
                        qsTr("Nothing is sent over the air. The board restarts and briefly disappears. Do not unplug it.")
                    ]
                    delegate: RowLayout {
                        required property string modelData
                        Layout.fillWidth: true; spacing: Look.space2
                        Glyph { name: "info"; size: 14; stroke: 2; color: Look.textSecondary; Layout.alignment: Qt.AlignTop }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: modelData }
                    }
                }
                TakLabel { text: qsTr("The plan") }
                Repeater {
                    model: root.fw.plan.steps || []
                    delegate: RowLayout {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true; spacing: Look.space3
                        Rectangle { Layout.alignment: Qt.AlignTop; implicitWidth: Look.space5 - Look.space1 * 0.5; implicitHeight: implicitWidth; radius: implicitWidth / 2; color: Look.field; border.width: 1; border.color: Look.hairline
                                    TaosText { anchors.centerIn: parent; text: index + 1; font.pixelSize: Look.caption; font.weight: Font.DemiBold } }
                        ColumnLayout { Layout.fillWidth: true; spacing: 0
                            TaosText { text: modelData.name === "backup" ? qsTr("Back up the current settings") : modelData.name === "flash" ? qsTr("Write the firmware") : modelData.name === "verify" ? qsTr("Check the board came up") : modelData.name === "restore" ? qsTr("Restore the settings") : modelData.name
                                       font.pixelSize: Look.body; font.weight: Font.DemiBold }
                            TaosText { text: modelData.detail; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary } }
                    }
                }
                Repeater {
                    objectName: "fwWarnings"
                    model: root.fw.plan.warnings || []
                    delegate: RowLayout {
                        required property string modelData
                        Layout.fillWidth: true; spacing: Look.space2
                        Glyph { name: "alert"; size: 14; stroke: 2; color: Look.warning; Layout.alignment: Qt.AlignTop }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.warning; text: modelData }
                    }
                }
                RowLayout {
                    visible: !!root.fw.plan.requires_unsigned_ack
                    Layout.fillWidth: true; spacing: Look.space2
                    TaosCheck { id: ackBox; objectName: "ackUnsigned"; Layout.alignment: Qt.AlignTop
                                checked: root.nav && root.nav.demo.ack !== undefined
                                accessibleName: qsTr("I have read this. The publisher does not sign this image and I want to flash it anyway.") }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body
                               text: qsTr("I have read this. The publisher does not sign this image and I want to flash it anyway.")
                               TapHandler { onTapped: ackBox.toggle() } }
                }
                TaosText { text: qsTr("Type %1 to confirm").arg(root.fw.boardName(root.fw.plan.board)); font.pixelSize: Look.body; font.weight: Font.DemiBold; Layout.topMargin: Look.space2 }
                TaosField { id: typed; objectName: "confirmField"; text: root.nav && root.nav.demo.typed !== undefined ? root.nav.demo.typed : ""; Layout.fillWidth: true
                            placeholderText: root.fw.boardName(root.fw.plan.board); accessibleName: qsTr("Type the board name to confirm") }
                TaosText { id: beginError; visible: text !== ""; objectName: "beginError"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.danger; text: "" }
                Flow {
                    Layout.fillWidth: true; spacing: Look.space2
                    PillButton { objectName: "switchButton"; variant: "primary"; text: qsTr("Switch the radio")
                                 enabled: typed.text === root.fw.boardName(root.fw.plan.board) && (!root.fw.plan.requires_unsigned_ack || ackBox.checked)
                                 onClicked: beginError.text = root.sw.begin(root.rowKey, root.entryId, typed.text, ackBox.checked) }
                    PillButton { text: qsTr("Cancel"); variant: "outline"; onClicked: { root.fw.clearPlan(); root.entryId = ""; root.chosen = "choose" } }
                }
            }
            PillButton { visible: root.fw.planError !== ""; compact: true; text: qsTr("Back"); variant: "outline"; onClicked: { root.fw.clearPlan(); root.entryId = ""; root.chosen = "choose" } }
        }

        // ---------------------------------------------------------------- it is happening, or it happened
        ColumnLayout {
            visible: root.phase === "run" || root.phase === "result"
            objectName: "switchProgress"
            Layout.fillWidth: true; spacing: Look.space3
            RowLayout {
                spacing: Look.space2
                Glyph { name: root.sw.step === "done" ? "check-circle" : root.sw.step === "failed" ? "alert" : "refresh"; size: 20; stroke: 1.8
                        color: root.sw.step === "done" ? Look.success : root.sw.step === "failed" ? Look.danger : Look.textSecondary }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold
                           text: root.sw.step === "done" ? qsTr("Done. The radio is on the new network.") : root.sw.step === "failed" ? qsTr("The switch stopped") : qsTr("Switching... %1").arg(root.sw.elapsed) }
            }
            TaosText { visible: root.sw.running; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Do not unplug the radio. Writing the firmware can take a few minutes and has no percentage; the elapsed time above is what is known.") }
            ProgressTrack { visible: root.sw.running; Layout.fillWidth: true; indeterminate: true }
            Repeater {
                model: root.sw.stages
                delegate: RowLayout {
                    required property var modelData
                    objectName: "stage-" + modelData.id
                    Layout.fillWidth: true; spacing: Look.space2
                    Glyph { name: modelData.state === "done" ? "check-circle" : modelData.state === "failed" ? "circle-x" : modelData.state === "active" ? "refresh" : "clock"; size: 14; stroke: 2
                            color: modelData.state === "done" ? Look.success : modelData.state === "failed" ? Look.danger : Look.textSecondary }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; font.weight: modelData.state === "active" ? Font.DemiBold : Font.Normal
                               color: modelData.state === "pending" ? Look.textTertiary : Look.text; text: modelData.label }
                }
            }
            Repeater {
                model: root.sw.notes
                delegate: TaosText { required property string modelData; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary; text: modelData }
            }
            ColumnLayout {
                visible: root.sw.step === "failed"
                Layout.fillWidth: true; spacing: Look.space2
                TaosText { objectName: "switchError"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.danger; text: root.sw.error }
                TakLabel { text: qsTr("Getting the radio back") }
                TaosText { objectName: "recoveryText"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: root.sw.recovery }
                PillButton { objectName: "putPreviousBack"; visible: root.sw.previous !== ""; compact: true; text: qsTr("Put the previous firmware back"); glyph: "refresh"
                             onClicked: { const id = root.sw.previous; root.sw.reset(); root.entryId = id; root.fw.planFlash(root.row.fwDeviceId, id); root.chosen = "review" } }
            }
            PillButton { compact: true; variant: "primary"; visible: !root.sw.running; text: qsTr("Done"); onClicked: { root.sw.reset(); root.fw.clearPlan(); root.nav.back() } }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
