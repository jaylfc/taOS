// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Radios: the ones the mesh engine has open (with who each belongs to and its settings), the USB devices found on this
// device, and the firmware library with the guarded switch of a board between networks.
C.ScrollView {
    id: root
    property var nav
    readonly property var tak: Survival.tak
    readonly property var radios: Survival.radios
    readonly property var fw: Survival.firmware
    property string assigning: ""
    Component.onCompleted: fw.reload()
    onNavChanged: if (nav && nav.demo.assign !== undefined) assigning = nav.demo.assign
    contentWidth: availableWidth
    clip: true
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space2
        TakLabel { text: qsTr("Connected to the engine"); Layout.topMargin: Look.space3; Layout.leftMargin: Look.space3 }
        EmptyState {
            visible: root.tak.radios.length === 0
            Layout.fillWidth: true; Layout.topMargin: Look.space3
            glyph: "radio"
            title: root.tak.available ? qsTr("The engine has no radio open") : qsTr("No mesh engine")
            detail: root.tak.available ? qsTr("Name a radio in the engine's settings file (TAOS_MESH_ARGS in ~/.config/taos-mesh/mesh.env), then restart the engine. The app never opens a radio on its own.") : qsTr("Radios the engine has open are listed here once it runs.")
        }
        Repeater {
            model: root.tak.radios
            delegate: GlassPanel {
                id: card
                required property var modelData
                objectName: "radio-" + modelData.id
                Layout.fillWidth: true
                implicitHeight: col.implicitHeight + Look.space3 * 2
                ColumnLayout {
                    id: col
                    anchors { fill: parent; margins: Look.space3 }
                    spacing: Look.space2
                    RowLayout {
                        spacing: Look.space3
                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            width: 36; height: 36; radius: Look.radiusCard - 4
                            color: Look.tint(card.modelData.state === "connected" ? Look.success : Look.textSecondary, 0.14)
                            border.width: 1; border.color: Look.tint(card.modelData.state === "connected" ? Look.success : Look.textSecondary, 0.3)
                            Glyph { anchors.centerIn: parent; name: "radio"; size: 18; stroke: 1.8; color: card.modelData.state === "connected" ? Look.success : Look.textSecondary }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            TaosText { text: card.modelData.name; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                            TaosText { text: card.modelData.id; font.pixelSize: Look.small; color: Look.textSecondary; font.family: "monospace" }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: Look.space2
                        Chip { text: card.modelData.networkName }
                        Chip { text: card.modelData.link === "usb" ? qsTr("USB") : card.modelData.link === "ble" ? qsTr("Bluetooth") : qsTr("Network") }
                        Chip { text: card.modelData.state === "connected" ? qsTr("Connected") : card.modelData.state === "connecting" ? qsTr("Connecting") : qsTr("Offline")
                               tone: card.modelData.state === "connected" ? "success" : card.modelData.state === "connecting" ? "warning" : "neutral" }
                        Chip { text: card.modelData.battery >= 0 ? qsTr("Battery %1%").arg(card.modelData.battery) : qsTr("Battery unknown"); glyph: "battery" }
                    }
                    RowLayout {
                        spacing: Look.space2
                        Glyph { name: card.modelData.assignedKind === "agent" ? "bot" : card.modelData.assignedKind === "person" ? "user" : card.modelData.assignedKind === "contact" ? "users" : "alert"
                                size: 14; stroke: 2; color: card.modelData.assignedKind === "none" ? Look.warning : Look.textSecondary }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body
                                   color: card.modelData.assignedKind === "none" ? Look.warning : Look.text
                                   text: card.modelData.assignedKind === "none" ? qsTr("Not assigned to anyone") : qsTr("Belongs to %1").arg(card.modelData.assignedName) }
                    }
                    RowLayout {
                        spacing: Look.space2
                        PillButton { objectName: "assignToggle-" + card.modelData.id; compact: true; text: root.assigning === card.modelData.id ? qsTr("Done") : qsTr("Assign")
                                     glyph: "user"; onClicked: root.assigning = root.assigning === card.modelData.id ? "" : card.modelData.id }
                        PillButton { objectName: "settings-" + card.modelData.id; compact: true; text: qsTr("Settings"); glyph: "cpu"; onClicked: root.nav.open("settings", card.modelData.id) }
                    }
                    TakAssign { visible: root.assigning === card.modelData.id; Layout.fillWidth: true; node: card.modelData.id; current: card.modelData.identity }
                }
            }
        }

        TakLabel { text: qsTr("Firmware"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3 }
        ListRow {
            Layout.fillWidth: true
            objectName: "firmwareRow"
            glyph: "download"
            title: qsTr("Firmware library")
            subtitle: root.fw.state === "offline" ? qsTr("The firmware manager is not running") : root.fw.library.length === 0 ? qsTr("Nothing stored yet")
                      : qsTr("%1 stored on this device").arg(root.fw.totalSize)
            detail: qsTr("Keep firmware for Meshtastic, MeshCore and RNode ready with no internet, and move a board between networks.")
            onClicked: root.nav.open("firmware", "")
            trailing: Component { Glyph { name: "chevron-right"; size: 16; stroke: 2; color: Look.textTertiary } }
        }

        TakLabel { text: qsTr("Found on this device"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3 }
        EmptyState {
            visible: root.radios.scanned && root.radios.radios.length === 0
            Layout.fillWidth: true; Layout.topMargin: Look.space3
            glyph: "usb"
            title: qsTr("No radio plugged in")
            detail: qsTr("Plug a radio into a USB port. It shows up here within a few seconds. Nothing is sent or changed on it.")
        }
        StableModel { id: radioIds; source: root.radios.radios.map(r => r.device) }
        Repeater {
            model: radioIds.value
            delegate: GlassPanel {
                id: rcard
                required property string modelData
                readonly property var rd: { root.radios.radios; for (const r of root.radios.radios) if (r.device === modelData) return r; return null }
                visible: rd !== null
                Layout.fillWidth: true
                implicitHeight: rd ? rcol.implicitHeight + Look.space3 * 2 : 0
                ColumnLayout {
                    id: rcol
                    anchors { fill: parent; margins: Look.space3 }
                    spacing: Look.space2
                    TaosText { text: rcard.rd && rcard.rd.name !== "" ? rcard.rd.name : qsTr("USB serial device"); Layout.fillWidth: true; elide: Text.ElideRight
                               font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                    TaosText { text: rcard.rd ? rcard.rd.device + "  " + rcard.rd.usbId : ""; Layout.fillWidth: true; elide: Text.ElideRight
                               font.pixelSize: Look.small; color: Look.textSecondary; font.family: "monospace" }
                    Flow {
                        Layout.fillWidth: true; spacing: Look.space2
                        Chip { visible: !!rcard.rd && rcard.rd.protocol === ""; text: rcard.rd && rcard.rd.confidence === "possible" ? qsTr("Possible radio") : qsTr("Unlikely a radio"); tone: rcard.rd && rcard.rd.confidence === "possible" ? "accent" : "neutral" }
                        Chip { visible: !!rcard.rd && rcard.rd.protocol !== ""; text: qsTr("Meshtastic"); tone: "success"; glyph: "check" }
                        Chip { visible: !!rcard.rd && rcard.rd.firmware !== ""; text: rcard.rd ? rcard.rd.firmware : ""; mono: true }
                        Chip { visible: !!rcard.rd && rcard.rd.inUse; text: qsTr("In use by another program"); tone: "warning" }
                    }
                    TaosText { visible: !!rcard.rd && rcard.rd.chip !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: rcard.rd ? rcard.rd.chip + (rcard.rd.boards !== "" ? " - " + rcard.rd.boards : "") : "" }
                    TaosText { visible: !!rcard.rd && !rcard.rd.probed; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                               text: qsTr("A USB ID only tells the chip, not what runs on it. Checking opens the port and asks for its configuration; some boards restart when a port is opened. It never transmits.") }
                    PillButton {
                        Layout.alignment: Qt.AlignRight
                        compact: true
                        text: rcard.rd && rcard.rd.probing ? qsTr("Checking...") : rcard.rd && rcard.rd.probed ? qsTr("Check again") : qsTr("Check this device")
                        enabled: !!rcard.rd && !rcard.rd.inUse && root.radios.probing === ""
                        onClicked: root.radios.probe(rcard.modelData)
                    }
                }
            }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
