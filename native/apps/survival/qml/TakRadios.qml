// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Radios: one card per physical radio, whether the mesh engine has it open or it is only plugged in. Each card says what
// the board is, which network it runs and which firmware version (read from the radio itself, never guessed), and offers
// Connect, Settings, Assign and "Switch network". Reticulum is shown as coming, with what it needs. The firmware library
// has its own page.
C.ScrollView {
    id: root
    property var nav
    readonly property var tak: Survival.tak
    readonly property var fw: Survival.firmware
    property string assigning: ""
    Component.onCompleted: { fw.reload(); fw.detect(); tak.discover() }
    onNavChanged: if (nav && nav.demo.assign !== undefined) assigning = nav.demo.assign
    contentWidth: availableWidth
    clip: true
    Timer { interval: 5000; repeat: true; running: root.visible && root.tak.connecting === ""; onTriggered: { root.tak.discover(); root.fw.detect() } }
    function stateText(r) {
        return r.state === "connected" ? qsTr("Connected") : r.state === "connecting" ? qsTr("Connecting") : r.state === "detected" ? qsTr("Not connected") : qsTr("Offline")
    }
    function stateTone(r) { return r.state === "connected" ? "success" : r.state === "connecting" ? "warning" : "neutral" }
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space2
        TakLabel { text: qsTr("Your radios"); Layout.topMargin: Look.space3; Layout.leftMargin: Look.space3 }
        EmptyState {
            visible: root.tak.radioRows.length === 0
            Layout.fillWidth: true; Layout.topMargin: Look.space3
            glyph: "usb"
            title: root.tak.available ? qsTr("No radio found") : qsTr("No mesh engine")
            detail: root.tak.available ? qsTr("Plug a radio into a USB port. It shows up here within a few seconds. Nothing is sent or changed on it until you ask.")
                                       : qsTr("Radios are listed here once the mesh engine runs.")
        }
        Repeater {
            model: root.tak.radioRows
            delegate: GlassPanel {
                id: card
                required property var modelData
                readonly property var r: modelData
                objectName: r.engineRadioId !== "" ? "radio-" + r.engineRadioId : "device-row-" + r.key
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
                            implicitWidth: Look.space6 + Look.space1; implicitHeight: implicitWidth; radius: Look.radiusControl
                            color: Look.tint(card.r.state === "connected" ? Look.success : Look.textSecondary, 0.14)
                            border.width: 1; border.color: Look.tint(card.r.state === "connected" ? Look.success : Look.textSecondary, 0.3)
                            Glyph { anchors.centerIn: parent; name: card.r.kind === "ble" ? "radio" : "usb"; size: 18; stroke: 1.8; color: card.r.state === "connected" ? Look.success : Look.textSecondary }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            TaosText { objectName: "radioTitle"; text: card.r.title; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                            TaosText { text: card.r.port !== "" ? card.r.port : (card.r.detail || card.r.key); Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: Look.small; color: Look.textSecondary; font.family: Look.monoFamily }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: Look.space2
                        Chip { objectName: "networkChip"; visible: card.r.network !== ""; text: card.r.networkName; tone: "accent" }
                        Chip { objectName: "firmwareChip"; visible: card.r.firmware !== ""; text: qsTr("Firmware %1").arg(card.r.firmware); mono: true }
                        Chip { objectName: "stateChip"; text: root.stateText(card.r); tone: root.stateTone(card.r) }
                        Chip { visible: card.r.kind === "ble"; text: qsTr("Bluetooth") }
                        Chip { visible: card.r.battery >= 0; text: qsTr("Battery %1%").arg(card.r.battery); glyph: "battery" }
                        Chip { visible: card.r.saved; text: qsTr("Opens again after a restart"); tone: "success"; glyph: "check" }
                    }
                    TaosText { visible: card.r.state === "detected" && card.r.network === ""; objectName: "notConnectedNote"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: card.r.boardGuess !== "" ? qsTr("Looks like a %1. Connect to read which network it runs and its firmware version. Connecting only reads; nothing is sent over the air.").arg(card.r.boardGuess)
                                                              : qsTr("Connect to read which network it runs and its firmware version. Connecting only reads; nothing is sent over the air.") }
                    TaosText { visible: card.r.busy; objectName: "handshake-" + card.r.key; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: qsTr("Opening the port and asking the radio who it is. This only reads; nothing is sent over the air.") }
                    TaosText { visible: card.r.error !== ""; objectName: "connectError-" + card.r.key; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.danger; text: card.r.error }
                    RowLayout {
                        visible: card.r.assignedKind !== undefined && card.r.engineRadioId !== ""
                        spacing: Look.space2
                        Glyph { name: card.r.assignedKind === "agent" ? "bot" : card.r.assignedKind === "person" ? "user" : card.r.assignedKind === "contact" ? "users" : "alert"
                                size: 14; stroke: 2; color: card.r.assignedKind === "none" ? Look.warning : Look.textSecondary }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body
                                   color: card.r.assignedKind === "none" ? Look.warning : Look.text
                                   text: card.r.assignedKind === "none" ? qsTr("Not assigned to anyone") : qsTr("Belongs to %1").arg(card.r.assignedName) }
                    }
                    TaosText { visible: card.r.profiles.length > 0; objectName: "savedProfiles"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: qsTr("Settings kept for: %1").arg(card.r.profiles.map(p => p.network === "meshtastic" ? "Meshtastic" : p.network === "meshcore" ? "MeshCore" : p.network).join(", ")) }
                    Flow {
                        Layout.fillWidth: true; spacing: Look.space2
                        PillButton { objectName: "connect-" + card.r.key; compact: true; variant: "primary"; glyph: "plug"
                                     visible: card.r.state === "detected" && card.r.kind !== "ble" && !card.r.saved
                                     text: card.r.busy ? qsTr("Connecting...") : qsTr("Connect")
                                     enabled: card.r.canConnect && root.tak.connecting === ""
                                     onClicked: root.tak.connectDevice(card.r.key) }
                        PillButton { compact: true; text: qsTr("Try again"); visible: card.r.error !== "" && card.r.kind !== "ble"; enabled: root.tak.connecting === ""; onClicked: root.tak.connectDevice(card.r.key) }
                        PillButton { objectName: "assignToggle-" + card.r.engineRadioId; compact: true; visible: card.r.engineRadioId !== ""
                                     text: root.assigning === card.r.engineRadioId ? qsTr("Done") : qsTr("Assign"); glyph: "user"
                                     onClicked: root.assigning = root.assigning === card.r.engineRadioId ? "" : card.r.engineRadioId }
                        PillButton { objectName: "settings-" + card.r.engineRadioId; compact: true; visible: card.r.canSettings; text: qsTr("Settings"); glyph: "cpu"; onClicked: root.nav.open("settings", card.r.engineRadioId) }
                        PillButton { objectName: "switch-" + (card.r.fwDeviceId !== "" ? card.r.fwDeviceId : card.r.key); compact: true; visible: card.r.kind !== "ble"; text: qsTr("Switch network..."); glyph: "refresh"
                                     enabled: card.r.canSwitch && !Survival.switcher.running
                                     onClicked: root.nav.open("switch", card.r.key) }
                        PillButton { objectName: "forget-" + card.r.key; compact: true; text: qsTr("Forget"); visible: card.r.saved && card.r.state === "detected"; onClicked: root.tak.forgetDevice(card.r.key) }
                    }
                    TaosText { visible: card.r.kind !== "ble" && !card.r.canSwitch && (card.r.switchWhy || "") !== ""; objectName: "switchWhy"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                               text: card.r.switchWhy || "" }
                    TakAssign { visible: root.assigning === card.r.engineRadioId && card.r.engineRadioId !== ""; Layout.fillWidth: true; node: card.r.engineRadioId; current: card.r.identity }
                }
            }
        }
        TaosText {
            visible: root.tak.radioRows.length > 0 && root.tak.radioRows.every(r => r.state !== "connected")
            Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
            text: qsTr("One radio per network works best: a Meshtastic radio and a MeshCore radio can be connected at the same time. With only one spare radio, use Switch network to move it between them.")
        }

        // ---- Reticulum: coming
        TakLabel { text: qsTr("Reticulum"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3 }
        GlassPanel {
            objectName: "reticulumComing"
            Layout.fillWidth: true
            implicitHeight: retCol.implicitHeight + Look.space3 * 2
            ColumnLayout {
                id: retCol
                anchors { fill: parent; margins: Look.space3 }
                spacing: Look.space2
                RowLayout {
                    spacing: Look.space2
                    Glyph { name: "clock"; size: 16; stroke: 1.8; color: Look.textSecondary }
                    TaosText { text: qsTr("Reticulum is coming"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                }
                Chip { text: qsTr("Not available yet") }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                           text: qsTr("There is no Reticulum driver in the mesh engine yet, so nothing can be connected or sent on it.") }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; font.weight: Font.DemiBold; text: qsTr("What it will need") }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                           text: qsTr("RNode firmware on a board (the firmware library already stores it, and Switch network will offer it once the driver exists), and the shared Survival Studio container, which will run Reticulum for every part of the app. Reticulum's licence is shown and accepted the first time it is fetched.") }
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
            detail: qsTr("Keep firmware for Meshtastic, MeshCore and RNode ready with no internet.")
            onClicked: root.nav.open("firmware", "")
            trailing: Component { Glyph { name: "chevron-right"; size: 16; stroke: 2; color: Look.textTertiary } }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
