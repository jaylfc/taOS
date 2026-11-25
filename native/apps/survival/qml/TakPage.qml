// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// taOStak: the awareness view on the same map. You, your markers and contacts, the radios plugged into this device,
// and messages. Sending needs the mesh engine, which is the next slice: nothing here transmits.
StagePage {
    id: page
    takMode: true
    pageTitle: qsTr("taOStak")
    property string tab: "people"
    readonly property var radios: Survival.radios
    function focusSearch() { stage.focusSearch() }
    Component.onCompleted: { map.startPosition(); radios.scan() }
    Timer { interval: 4000; repeat: true; running: page.visible && page.tab === "radios"; onTriggered: page.radios.scan() }
    onTabChanged: if (tab === "radios") radios.scan()

    sidePanel: Component {
        ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Look.space4
                spacing: Look.space3
                TaosText { text: qsTr("taOStak"); Layout.fillWidth: true; font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            }
            Segmented {
                Layout.fillWidth: true
                Layout.leftMargin: Look.space4; Layout.rightMargin: Look.space4; Layout.bottomMargin: Look.space3
                accessibleName: qsTr("taOStak panel")
                model: [{id: "people", text: qsTr("People")}, {id: "radios", text: qsTr("Radios")}, {id: "messages", text: qsTr("Messages")}]
                currentId: page.tab
                onActivated: (id) => page.tab = id
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
            // ---- people
            C.ScrollView {
                visible: page.tab === "people"
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    x: Look.space3
                    width: parent.width - Look.space3 * 2
                    spacing: Look.space1
                    TaosText { text: qsTr("You"); Layout.topMargin: Look.space3; Layout.leftMargin: Look.space3; font.pixelSize: Look.small; font.weight: Font.DemiBold
                               color: Look.textTertiary; font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0 }
                    ListRow {
                        Layout.fillWidth: true
                        glyph: "user"
                        tone: page.map.hasMe ? "accent" : "neutral"
                        clickable: page.map.hasMe
                        title: page.map.hasMe ? page.map.formatCoords(page.map.meLon, page.map.meLat) : qsTr("Position not set")
                        subtitle: page.map.meSource === "device" ? qsTr("From this device") : page.map.meSource === "manual" ? qsTr("Set by hand") : page.map.positionAvailable ? qsTr("Waiting for this device to report a position") : qsTr("No GPS or location service found")
                        onClicked: page.stage.centerOn(page.map.meLon, page.map.meLat)
                    }
                    RowLayout {
                        Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; spacing: Look.space2
                        PillButton { Layout.fillWidth: true; compact: true; text: qsTr("Set on the map"); glyph: "map-pin"
                                     onClicked: { page.stage.pickingMe = true; page.stage.forceActiveFocus() } }
                        PillButton { visible: page.map.meSource === "manual"; compact: true; text: qsTr("Clear"); onClicked: page.map.clearMe() }
                    }
                    TaosText { text: qsTr("Contacts"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3; font.pixelSize: Look.small; font.weight: Font.DemiBold
                               color: Look.textTertiary; font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0 }
                    Repeater {
                        model: page.map.contacts
                        delegate: ListRow {
                            required property var modelData
                            Layout.fillWidth: true
                            glyph: "users"; clickable: false
                            title: modelData.callsign
                            subtitle: modelData.nodeId !== "" ? qsTr("Node %1").arg(modelData.nodeId) : qsTr("No node id")
                            detail: qsTr("Position unknown: no radio link yet")
                            trailing: Component {
                                TaosButton { glyph: "trash"; compact: true; variant: "ghost"; accessibleName: qsTr("Remove contact %1").arg(modelData.callsign)
                                             onClicked: page.map.removeContact(modelData.id) }
                            }
                        }
                    }
                    EmptyState {
                        visible: page.map.contacts.length === 0
                        Layout.fillWidth: true; Layout.topMargin: Look.space4
                        glyph: "users"; title: qsTr("No contacts yet")
                        detail: qsTr("Add the people and devices you expect on the mesh. Their positions appear on the map once the mesh engine is connected.")
                    }
                    RowLayout {
                        Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; Layout.topMargin: Look.space3; spacing: Look.space2
                        TaosField { id: callsign; Layout.fillWidth: true; placeholderText: qsTr("Callsign or name"); accessibleName: qsTr("Contact name"); onAccepted: addBtn.clicked() }
                        TaosField { id: nodeId; Layout.preferredWidth: 96; placeholderText: qsTr("Node id"); accessibleName: qsTr("Contact node id, optional"); onAccepted: addBtn.clicked() }
                        TaosButton { id: addBtn; glyph: "plus"; variant: "primary"; enabled: callsign.text.trim() !== ""; accessibleName: qsTr("Add contact")
                                     onClicked: { page.map.addContact(callsign.text, nodeId.text, ""); callsign.text = ""; nodeId.text = "" } }
                    }
                    Item { Layout.preferredHeight: Look.space4 }
                }
            }
            // ---- radios
            C.ScrollView {
                visible: page.tab === "radios"
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    x: Look.space3
                    width: parent.width - Look.space3 * 2
                    spacing: Look.space2
                    EmptyState {
                        visible: page.radios.scanned && page.radios.radios.length === 0
                        Layout.fillWidth: true; Layout.topMargin: Look.space5
                        glyph: "radio"
                        title: qsTr("No radio connected")
                        detail: qsTr("Plug a Meshtastic radio into a USB port. It shows up here within a few seconds. Nothing is sent or changed on it.")
                    }
                    StableModel { id: radioIds; source: page.radios.radios.map(r => r.device) }
                    Repeater {
                        model: radioIds.value
                        delegate: GlassPanel {
                            id: rcard
                            required property string modelData
                            readonly property var rd: { page.radios.radios; for (const r of page.radios.radios) if (r.device === modelData) return r; return ({}) }
                            Layout.fillWidth: true
                            Layout.topMargin: Look.space2
                            implicitHeight: rcol.implicitHeight + Look.space3 * 2
                            ColumnLayout {
                                id: rcol
                                anchors { fill: parent; margins: Look.space3 }
                                spacing: Look.space2
                                RowLayout {
                                    spacing: Look.space3
                                    Rectangle {
                                        Layout.alignment: Qt.AlignTop
                                        width: 36; height: 36; radius: Look.radiusCard - 4
                                        color: rcard.rd.protocol !== "" ? Look.tint(Look.success, 0.14) : Look.field
                                        border.width: 1; border.color: rcard.rd.protocol !== "" ? Look.tint(Look.success, 0.3) : Look.hairline
                                        Glyph { anchors.centerIn: parent; name: "radio"; size: 18; stroke: 1.8; color: rcard.rd.protocol !== "" ? Look.success : Look.textSecondary }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 2
                                        TaosText { text: rcard.rd.name !== "" ? rcard.rd.name : qsTr("USB serial device"); Layout.fillWidth: true; elide: Text.ElideRight
                                                   font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                                        TaosText { text: rcard.rd.device + "  " + rcard.rd.usbId; Layout.fillWidth: true; elide: Text.ElideRight
                                                   font.pixelSize: Look.small; color: Look.textSecondary; font.family: "monospace" }
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true; spacing: Look.space2
                                    Chip { visible: rcard.rd.protocol === ""; text: rcard.rd.confidence === "possible" ? qsTr("Possible radio") : qsTr("Unlikely a radio"); tone: rcard.rd.confidence === "possible" ? "accent" : "neutral" }
                                    Chip { visible: rcard.rd.protocol !== ""; text: qsTr("Meshtastic"); tone: "success"; glyph: "check" }
                                    Chip { visible: rcard.rd.firmware !== ""; text: rcard.rd.firmware; mono: true }
                                    Chip { visible: rcard.rd.inUse; text: qsTr("In use by another program"); tone: "warning" }
                                }
                                TaosText {
                                    visible: rcard.rd.chip !== ""
                                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                    text: rcard.rd.chip + (rcard.rd.boards !== "" ? " - " + rcard.rd.boards : "")
                                }
                                TaosText { visible: rcard.rd.note !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small
                                           color: rcard.rd.protocol !== "" ? Look.success : Look.textSecondary; text: rcard.rd.note }
                                TaosText {
                                    visible: !rcard.rd.probed
                                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                                    text: qsTr("A USB ID only tells the chip, not what runs on it. Checking opens the port and asks for its configuration; some boards restart when a port is opened. It never transmits.")
                                }
                                PillButton {
                                    Layout.alignment: Qt.AlignRight
                                    compact: true
                                    text: rcard.rd.probing ? qsTr("Checking...") : rcard.rd.probed ? qsTr("Check again") : qsTr("Check this device")
                                    enabled: !rcard.rd.inUse && page.radios.probing === ""
                                    accessibleName: qsTr("Check whether %1 is a Meshtastic radio").arg(rcard.rd.device)
                                    onClicked: page.radios.probe(rcard.rd.device)
                                }
                            }
                        }
                    }
                    TaosText {
                        visible: page.radios.radios.length > 0
                        Layout.fillWidth: true; Layout.topMargin: Look.space2; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                        text: qsTr("MeshCore and Reticulum radios are not detected yet.")
                    }
                    Item { Layout.preferredHeight: Look.space4 }
                }
            }
            // ---- messages
            ColumnLayout {
                visible: page.tab === "messages"
                Layout.fillWidth: true; Layout.fillHeight: true
                spacing: 0
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    EmptyState {
                        anchors.centerIn: parent
                        width: parent.width - Look.space6
                        glyph: "message"
                        title: qsTr("No messages")
                        detail: Survival.meshAvailable ? qsTr("No messages yet.") : qsTr("Messaging needs the mesh engine, which is the next piece of work. When it is connected, direct and team messages appear here with their delivery state.")
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
                RowLayout {
                    Layout.fillWidth: true; Layout.margins: Look.space3; spacing: Look.space2
                    TaosField { id: msgField; Layout.fillWidth: true; enabled: Survival.meshAvailable; placeholderText: Survival.meshAvailable ? qsTr("Message") : qsTr("Sending needs the mesh engine"); accessibleName: qsTr("Message"); onAccepted: sendBtn.clicked() }
                    TaosButton { id: sendBtn; glyph: "send"; variant: "primary"; enabled: Survival.meshAvailable && msgField.text.trim() !== ""; accessibleName: qsTr("Send"); onClicked: { if (Survival.meshSend("", msgField.text) === "") msgField.text = "" } }
                }
            }
        }
    }
}
