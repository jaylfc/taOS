// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The firmware library: what is stored ahead of need so a board can be flashed with no internet, the licences, and the
// boards the firmware manager sees. Moving a board to another network is the Switch network page (it also saves the
// radio's settings and reconnects it afterwards), opened from here or from the Radios page.
C.ScrollView {
    id: root
    property var nav
    readonly property var fw: Survival.firmware
    contentWidth: availableWidth
    clip: true
    Component.onCompleted: { fw.reload(); fw.detect() }
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
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space3
        // ---------------------------------------------------------------- the library
        ColumnLayout {
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
                                     onClicked: root.nav.open("switch", dev.modelData.port || dev.modelData.id) }
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

        Item { Layout.preferredHeight: Look.space4 }
    }
}
