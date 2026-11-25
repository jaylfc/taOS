// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The firmware library and the guarded switch of a board between networks. The library is stored ahead of need so a
// board can be flashed with no internet. Switching walks one board through: detect, choose the network, plan (nothing is
// written), back up, type the board name, flash, verify. A failed flash shows the board's own recovery steps.
C.ScrollView {
    id: root
    property var nav
    readonly property var fw: Survival.firmware
    property string step: "list"          // list | choose | plan | result
    property string deviceId: ""
    property string entryId: ""
    property bool noBackupOk: false
    contentWidth: availableWidth
    clip: true
    Component.onCompleted: { fw.reload(); fw.detect() }
    // scripted states for screenshots: --demo fwstep=choose|plan|result;typed=<text>;flash=1
    onNavChanged: if (nav && nav.demo.fwstep !== undefined) Qt.callLater(demoStep)
    function demoStep() {
        const d = nav.demo
        if (!fw.devices.length) { Qt.callLater(demoStep); return }
        deviceId = fw.devices[0].id
        if (d.fwstep === "choose") step = "choose"
        else { entryId = "meshtastic:heltec-v3:2.7.26.54e0d8d"; fw.planFlash(deviceId, entryId); step = "plan"
               if (d.fwstep === "result") Qt.callLater(demoRun) }
    }
    function demoRun() {
        if (!fw.plan.confirm_phrase) { Qt.callLater(demoRun); return }
        fw.runFlash(deviceId, entryId, fw.plan.confirm_phrase, false); step = "result"
    }
    function networkName(n) { return n === "meshtastic" ? "Meshtastic" : n === "meshcore" ? "MeshCore" : n === "rnode" ? "RNode (Reticulum)" : n }
    function boardName(id) { for (const b of (fw.boards.boards || [])) if (b.id === id) return b.name; return id }
    // one group per network, one row per board: its versions and total size; how the network's files were checked is said once
    readonly property var groups: {
        const by = {}
        for (const e of fw.library) {
            if (!by[e.network]) by[e.network] = {network: e.network, how: e.verifiedBy, unsigned: false, boards: {}}
            const g = by[e.network]
            if (e.unsigned) g.unsigned = true
            if (!g.boards[e.board]) g.boards[e.board] = {board: e.board, versions: [], bytes: 0}
            const b = g.boards[e.board]
            if (b.versions.indexOf(e.version) < 0) b.versions.push(e.version)
            b.bytes += e.sizeBytes
        }
        return Object.keys(by).sort().map(n => ({network: n, how: by[n].how, unsigned: by[n].unsigned, rows: Object.keys(by[n].boards).sort().map(k => by[n].boards[k])}))
    }
    readonly property var device: { for (const d of fw.devices) if (d.id === deviceId) return d; return null }
    // for the chosen board: the newest version of each network that fits it
    readonly property var choices: {
        if (!device) return []
        const best = {}
        for (const e of fw.library) {
            if (device.candidates.indexOf(e.board) < 0) continue
            const k = e.network + "|" + e.board + "|" + e.variant
            if (!best[k]) best[k] = e
        }
        return Object.keys(best).sort().map(k => best[k])
    }
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space3
        // ---------------------------------------------------------------- the library
        ColumnLayout {
            visible: root.step === "list"
            Layout.fillWidth: true; spacing: Look.space3
            GlassPanel {
                visible: root.fw.state === "offline"
                objectName: "firmwareDown"
                Layout.fillWidth: true; Layout.topMargin: Look.space3
                implicitHeight: dCol.implicitHeight + Look.space3 * 2
                ColumnLayout {
                    id: dCol
                    anchors { fill: parent; margins: Look.space3 }
                    spacing: Look.space2
                    RowLayout { spacing: Look.space2
                        Glyph { name: "download"; size: 18; stroke: 1.8; color: Look.textSecondary }
                        TaosText { text: qsTr("The firmware manager is not running"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap } }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: qsTr("It keeps the firmware library and does the flashing. It is not started on its own.") }
                    RowLayout { spacing: Look.space2
                        PillButton { compact: true; variant: "primary"; text: qsTr("Start it"); glyph: "play"; onClicked: root.fw.startService() }
                        PillButton { compact: true; text: qsTr("Check again"); glyph: "refresh"; onClicked: { root.fw.reload(); root.fw.detect() } } }
                }
            }
            RowLayout {
                visible: root.fw.state !== "offline"
                Layout.fillWidth: true; Layout.topMargin: Look.space3; spacing: Look.space2
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 0
                    TaosText { text: qsTr("Stored firmware"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                    TaosText { text: root.fw.library.length === 0 ? qsTr("Nothing stored yet") : qsTr("%1 on this device").arg(root.fw.totalSize); font.pixelSize: Look.small; color: Look.textSecondary }
                }
                PillButton { objectName: "fwRefresh"; compact: true; glyph: "refresh"; text: root.fw.busy ? qsTr("Refreshing...") : qsTr("Refresh"); enabled: !root.fw.busy && root.fw.pending.length === 0
                             onClicked: root.fw.refreshLibrary() }
            }
            TaosText { visible: root.fw.refreshNote !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary; text: root.fw.refreshNote }

            // licences: shown once, accepted once per network
            Repeater {
                model: root.fw.pending
                delegate: GlassPanel {
                    id: lic
                    required property var modelData
                    objectName: "licence-" + modelData.network
                    Layout.fillWidth: true
                    implicitHeight: licCol.implicitHeight + Look.space3 * 2
                    ColumnLayout {
                        id: licCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout { spacing: Look.space2
                            Glyph { name: "shield"; size: 16; stroke: 1.8; color: Look.warning }
                            TaosText { text: qsTr("%1 firmware licence").arg(lic.modelData.name); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap } }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                   text: qsTr("This firmware is published by its own project under %1. taOS does not ship it. It is downloaded only after you accept, and your acceptance is recorded once.").arg(lic.modelData.licence_id) }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; font.pixelSize: Look.small; font.family: "monospace"; text: lic.modelData.licence_url }
                        PillButton { objectName: "acceptLicence-" + lic.modelData.network; compact: true; variant: "primary"; text: qsTr("I accept the %1 licence").arg(lic.modelData.licence_id)
                                     onClicked: root.fw.acceptLicence(lic.modelData.network) }
                    }
                }
            }
            Repeater {
                model: root.fw.accepted
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: Look.space2
                    Chip { Layout.alignment: Qt.AlignTop; text: root.networkName(modelData.network); tone: "success"; glyph: "check" }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: qsTr("%1, accepted by %2 on %3").arg(modelData.licence_id).arg(modelData.accepted_by).arg(root.fw.dateText(modelData.accepted_at)) }
                }
            }

            EmptyState {
                visible: root.fw.state === "ready" && root.fw.library.length === 0 && root.fw.pending.length === 0
                Layout.fillWidth: true; Layout.topMargin: Look.space3
                glyph: "download"; title: qsTr("No firmware stored yet")
                detail: qsTr("Refresh while you are online. The latest stable and the one before it are kept for each network.")
            }
            Repeater {
                model: root.groups
                delegate: ColumnLayout {
                    id: grp
                    required property var modelData
                    Layout.fillWidth: true; spacing: Look.space1
                    objectName: "fwgroup-" + modelData.network
                    RowLayout {
                        Layout.fillWidth: true; Layout.topMargin: Look.space2; spacing: Look.space2
                        TakLabel { text: root.networkName(grp.modelData.network); Layout.leftMargin: Look.space1 }
                        Item { Layout.fillWidth: true }
                        Chip { text: grp.modelData.unsigned ? qsTr("Not signed by the publisher") : qsTr("Publisher checksums"); tone: grp.modelData.unsigned ? "warning" : "success"; glyph: grp.modelData.unsigned ? "alert" : "check" }
                    }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary; Layout.leftMargin: Look.space1
                               text: grp.modelData.unsigned ? qsTr("%1. Every file is verified before it is stored and again before a flash.").arg(grp.modelData.how) : qsTr("%1.").arg(grp.modelData.how) }
                    Repeater {
                        model: grp.modelData.rows
                        delegate: ListRow {
                            required property var modelData
                            Layout.fillWidth: true
                            objectName: "fw-" + grp.modelData.network + "|" + modelData.board
                            clickable: false
                            glyph: "cpu"
                            title: root.boardName(modelData.board)
                            subtitle: qsTr("Version %1").arg(modelData.versions.join(", "))
                            trailing: Component { TaosText { text: root.fw.sizeText(modelData.bytes); font.pixelSize: Look.small; color: Look.textSecondary; font.features: { "tnum": 1 } } }
                        }
                    }
                }
            }

            // ---- the switch
            TakLabel { text: qsTr("Switch a board's network"); Layout.topMargin: Look.space2 }
            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Moves a supported board between Meshtastic, MeshCore and RNode. Nothing is written until you type the board's name.") }
            Repeater {
                model: root.fw.devices
                delegate: GlassPanel {
                    id: dev
                    required property var modelData
                    objectName: "device-" + modelData.id
                    Layout.fillWidth: true
                    implicitHeight: devCol.implicitHeight + Look.space3 * 2
                    ColumnLayout {
                        id: devCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout { spacing: Look.space3
                            Glyph { name: "usb"; size: 18; stroke: 1.8; color: Look.textSecondary }
                            ColumnLayout { Layout.fillWidth: true; spacing: 2
                                TaosText { text: dev.modelData.product || qsTr("Radio board"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                                TaosText { text: (dev.modelData.port || dev.modelData.mount || "") + "  " + dev.modelData.vid + ":" + dev.modelData.pid; font.pixelSize: Look.small; color: Look.textSecondary; font.family: "monospace"; Layout.fillWidth: true; elide: Text.ElideRight } }
                        }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                   text: qsTr("Could be: %1").arg(dev.modelData.candidates.map(c => root.boardName(c)).join(", ")) }
                        PillButton { objectName: "switch-" + dev.modelData.id; compact: true; text: qsTr("Switch network"); glyph: "refresh"; enabled: root.fw.library.length > 0
                                     onClicked: { root.deviceId = dev.modelData.id; root.fw.clearPlan(); root.fw.clearResult(); root.step = "choose" } }
                    }
                }
            }
            EmptyState {
                visible: root.fw.state !== "offline" && root.fw.devices.length === 0
                Layout.fillWidth: true
                glyph: "usb"; title: qsTr("No board found")
                detail: qsTr("Plug in a supported board, or put it in its bootloader. Looking does not restart it.")
            }
            PillButton { visible: root.fw.state !== "offline"; compact: true; text: qsTr("Look again"); glyph: "search"; onClicked: root.fw.detect() }
        }

        // ---------------------------------------------------------------- choose the network
        ColumnLayout {
            visible: root.step === "choose"
            Layout.fillWidth: true; Layout.topMargin: Look.space3; spacing: Look.space2
            TaosText { text: qsTr("Choose the network"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Only firmware stored for this board is offered. Checking the plan may restart the board briefly.") }
            Repeater {
                model: root.choices
                delegate: ListRow {
                    required property var modelData
                    Layout.fillWidth: true
                    objectName: "choice-" + modelData.id
                    glyph: "radio"
                    title: root.networkName(modelData.network) + (modelData.variant !== "" ? " (" + modelData.variant.toUpperCase() + ")" : "")
                    subtitle: qsTr("Version %1, %2").arg(modelData.version).arg(root.fw.sizeText(modelData.sizeBytes))
                    onClicked: { root.entryId = modelData.id; root.fw.planFlash(root.deviceId, modelData.id); root.step = "plan" }
                    trailing: Component { Glyph { name: "chevron-right"; size: 16; stroke: 2; color: Look.textTertiary } }
                }
            }
            TaosText { visible: root.choices.length === 0; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                       text: qsTr("Nothing stored fits this board yet. Refresh the library first.") }
            PillButton { compact: true; text: qsTr("Back"); variant: "outline"; onClicked: root.step = "list" }
        }

        // ---------------------------------------------------------------- the plan and the confirmation
        ColumnLayout {
            visible: root.step === "plan"
            Layout.fillWidth: true; Layout.topMargin: Look.space3; spacing: Look.space3
            property var plan: root.fw.plan
            TaosText { text: qsTr("The plan"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
            TaosText { visible: root.fw.planError !== ""; objectName: "planError"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.danger; text: root.fw.planError }
            TaosText { visible: root.fw.planError === "" && !root.fw.plan.confirm_phrase; Layout.fillWidth: true; font.pixelSize: Look.body; color: Look.textSecondary; text: qsTr("Checking the board...") }
            ColumnLayout {
                visible: !!root.fw.plan.confirm_phrase
                Layout.fillWidth: true; spacing: Look.space2
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.bodyMedium
                           text: qsTr("%1 to %2 %3. Nothing has been written yet.").arg(root.fw.plan.confirm_phrase).arg(root.networkName(root.fw.plan.network || "")).arg(root.fw.plan.version || "") }
                Repeater {
                    model: root.fw.plan.steps || []
                    delegate: RowLayout {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true; spacing: Look.space3
                        Rectangle { Layout.alignment: Qt.AlignTop; width: 22; height: 22; radius: 11; color: Look.field; border.width: 1; border.color: Look.hairline
                                    TaosText { anchors.centerIn: parent; text: index + 1; font.pixelSize: Look.caption; font.weight: Font.DemiBold } }
                        ColumnLayout { Layout.fillWidth: true; spacing: 0
                            TaosText { text: modelData.name === "backup" ? qsTr("Back up the current settings") : modelData.name === "flash" ? qsTr("Write the firmware") : modelData.name === "verify" ? qsTr("Check the board came up") : modelData.name === "restore" ? qsTr("Restore the settings") : modelData.name
                                       font.pixelSize: Look.body; font.weight: Font.DemiBold }
                            TaosText { text: modelData.detail; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary } }
                    }
                }
                Repeater {
                    model: root.fw.plan.warnings || []
                    delegate: RowLayout {
                        required property string modelData
                        Layout.fillWidth: true; spacing: Look.space2
                        Glyph { name: "alert"; size: 14; stroke: 2; color: Look.warning; Layout.alignment: Qt.AlignTop }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.warning; text: modelData }
                    }
                }
                TaosCheck { id: noBackup; visible: false; text: qsTr("Flash even if the settings cannot be backed up") }
                TaosText { text: qsTr("Type %1 to confirm").arg(root.fw.plan.confirm_phrase); font.pixelSize: Look.body; font.weight: Font.DemiBold; Layout.topMargin: Look.space2 }
                TaosField { id: typed; objectName: "confirmField"; text: root.nav && root.nav.demo.typed !== undefined ? root.nav.demo.typed : ""; Layout.fillWidth: true; placeholderText: root.fw.plan.confirm_phrase || ""; accessibleName: qsTr("Type the board name to confirm") }
                RowLayout {
                    spacing: Look.space2
                    PillButton { objectName: "flashButton"; variant: "primary"; text: root.fw.busy ? qsTr("Flashing...") : qsTr("Flash the board")
                                 enabled: !root.fw.busy && typed.text === root.fw.plan.confirm_phrase
                                 onClicked: { root.step = "result"; root.fw.runFlash(root.deviceId, root.entryId, typed.text, false) } }
                    PillButton { text: qsTr("Cancel"); variant: "outline"; enabled: !root.fw.busy; onClicked: { root.fw.clearPlan(); root.step = "list" } }
                }
            }
            PillButton { visible: root.fw.planError !== ""; compact: true; text: qsTr("Back"); variant: "outline"; onClicked: { root.fw.clearPlan(); root.step = "list" } }
        }

        // ---------------------------------------------------------------- the result
        ColumnLayout {
            visible: root.step === "result"
            Layout.fillWidth: true; Layout.topMargin: Look.space3; spacing: Look.space3
            readonly property var r: root.fw.result
            readonly property bool done: !root.fw.busy && r.ok !== undefined
            TaosText { visible: !parent.done; font.pixelSize: Look.bodyMedium; color: Look.textSecondary; text: qsTr("Writing to the board. Do not unplug it.") }
            ColumnLayout {
                visible: parent.done
                Layout.fillWidth: true; spacing: Look.space2
                objectName: "flashResult"
                RowLayout { spacing: Look.space2
                    Glyph { name: root.fw.result.ok ? "check-circle" : "alert"; size: 20; stroke: 1.8; color: root.fw.result.ok ? Look.success : Look.danger }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold
                               text: root.fw.result.ok ? (root.fw.result.verify && root.fw.result.verify.status === "confirmed" ? qsTr("Done. The board came up on the new network.") : qsTr("Written. The board could not be checked from here."))
                                                       : qsTr("The flash did not complete") } }
                TaosText { visible: !root.fw.result.ok; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.danger; text: root.fw.result.message || "" }
                Repeater {
                    model: root.fw.result.steps || []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: Look.space2
                        Glyph { name: modelData.ok === false ? "circle-x" : "check-circle"; size: 14; stroke: 2; color: modelData.ok === false ? Look.danger : Look.success }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: modelData.name === "backup" ? qsTr("Settings backed up") : modelData.name === "flash" ? qsTr("Firmware written") : modelData.name === "verify" ? qsTr("Board checked") : modelData.name === "restore" ? qsTr("Settings restored") : modelData.name }
                    }
                }
                TaosText { visible: !!root.fw.result.restore && root.fw.result.restore.status === "kept_for_switching_back"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                           text: qsTr("The old settings are kept so you can switch back.") }
                ColumnLayout {
                    visible: !root.fw.result.ok && !!root.fw.result.recovery
                    Layout.fillWidth: true; spacing: Look.space1
                    TakLabel { text: qsTr("Getting the board back") }
                    TaosText { objectName: "recoveryText"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: root.fw.result.recovery || "" }
                }
                PillButton { compact: true; text: qsTr("Done"); variant: "primary"; onClicked: { root.fw.clearResult(); root.fw.clearPlan(); root.step = "list"; root.fw.detect() } }
            }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
