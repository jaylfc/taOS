// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The packs of one kind: what can be downloaded, what is installed, and how to import a file that is already on the
// device. Download shows progress and can be cancelled; a finished file is checked against the publisher's checksum
// and refused if it does not match. Delete asks twice. Nothing here is a mock: every button calls the pack engine.
ColumnLayout {
    id: root
    property string kind: "zim"
    property bool compact: false
    signal openRequested(string id)
    readonly property var engine: Survival.packs
    // the card list follows the ids only; each card reads its own pack, so a progress tick never rebuilds a card
    // (and never drops a pressed button or a half-done Delete confirmation)
    StableModel { id: ids; source: { root.engine.packs; return root.engine.ofKind(root.kind).map(p => p.id) } }
    spacing: Look.space3

    Repeater {
        model: ids.value
        delegate: GlassPanel {
            id: card
            required property string modelData
            readonly property var pk: { root.engine.packs; return root.engine.pack(modelData) }
            property bool confirming: false
            property string verifyText: ""
            property bool accepted: pk.licenceAccepted || !pk.needsAcceptance
            Layout.fillWidth: true
            implicitHeight: body.implicitHeight + Look.space4 * 2
            objectName: "pack-" + modelData
            Timer { id: confirmTimer; interval: 4000; onTriggered: card.confirming = false }
            ColumnLayout {
                id: body
                anchors { fill: parent; margins: Look.space4 }
                spacing: Look.space3
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Look.space3
                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        width: 40; height: 40; radius: Look.radiusCard - 3
                        color: Look.field; border.width: 1; border.color: Look.hairline
                        Glyph { anchors.centerIn: parent; name: root.kind === "map" ? "map" : "book"; size: 20; stroke: 1.8; color: Look.textSecondary }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        TaosText { text: card.pk.title; Layout.fillWidth: true; wrapMode: Text.Wrap
                                   font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                        TaosText { visible: card.pk.description !== ""; text: card.pk.description; Layout.fillWidth: true
                                   wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                        Flow {
                            Layout.fillWidth: true
                            Layout.topMargin: 4
                            spacing: Look.space2
                            Chip { visible: card.pk.installed; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                            Chip { visible: card.pk.sizeText !== "" && !card.pk.installed; text: card.pk.sizeText; glyph: "download" }
                            Chip { visible: card.pk.installed && card.pk.installedText !== ""; text: card.pk.installedText; glyph: "hard-drive" }
                            Chip { visible: card.pk.licence !== ""; text: card.pk.licence; glyph: "info"
                                   accessibleName: qsTr("Licence %1").arg(card.pk.licence) }
                            Chip { visible: card.pk.language !== ""; text: card.pk.language.toUpperCase() }
                            Chip { visible: card.pk.local; text: qsTr("Your file") }
                        }
                        TaosText {
                            visible: card.pk.attribution !== ""
                            Layout.fillWidth: true
                            Layout.topMargin: 2
                            text: card.pk.attribution
                            wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                        }
                    }
                }
                // not installed, no publisher download: how to make it
                ColumnLayout {
                    visible: !card.pk.installed && !card.pk.hasDownload && card.pk.recipe !== ""
                    Layout.fillWidth: true
                    spacing: Look.space2
                    TaosText { text: qsTr("There is no ready-made download for this map. Cut your own piece and import it:")
                               Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: recipeText.implicitHeight + Look.space3 * 2
                        radius: Look.radiusControl; color: Look.field; border.width: 1; border.color: Look.hairline
                        TextEdit {
                            id: recipeText
                            anchors { fill: parent; margins: Look.space3 }
                            readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                            text: card.pk.recipe
                            font.family: "monospace"; font.pixelSize: Look.small; color: Look.textSecondary
                            selectionColor: Look.selection
                            Accessible.role: Accessible.StaticText
                            Accessible.name: qsTr("Command to make a map pack")
                        }
                    }
                }
                // downloading
                ColumnLayout {
                    visible: card.pk.state === "downloading" || card.pk.state === "verifying"
                    Layout.fillWidth: true
                    spacing: 6
                    ProgressTrack { Layout.fillWidth: true; value: card.pk.progress; indeterminate: card.pk.state === "verifying" }
                    RowLayout {
                        Layout.fillWidth: true
                        TaosText {
                            Layout.fillWidth: true
                            font.pixelSize: Look.small; color: Look.textSecondary
                            text: card.pk.state === "verifying" ? qsTr("Checking the file against its checksum")
                                : qsTr("%1 of %2").arg(card.pk.receivedText || "0 B").arg(card.pk.sizeText)
                        }
                        TaosButton { visible: card.pk.state === "downloading"; text: qsTr("Pause"); compact: true
                                     accessibleName: qsTr("Pause the download of %1").arg(card.pk.title)
                                     onClicked: root.engine.cancel(card.pk.id) }
                    }
                }
                // failed
                RowLayout {
                    visible: card.pk.error !== ""
                    Layout.fillWidth: true
                    spacing: Look.space2
                    Glyph { name: "alert"; size: 16; stroke: 2; color: Look.danger; Layout.alignment: Qt.AlignTop }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: card.pk.error; font.pixelSize: Look.body; color: Look.danger }
                }
                // licence acceptance
                TaosCheck {
                    visible: card.pk.needsAcceptance && !card.pk.installed
                    text: qsTr("I accept the %1 licence").arg(card.pk.licence)
                    checked: card.accepted
                    onToggled: if (checked) root.engine.acceptLicence(card.pk.id)
                }
                TaosText { visible: card.verifyText !== ""; text: card.verifyText; Layout.fillWidth: true; wrapMode: Text.Wrap
                           font.pixelSize: Look.body; color: Look.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Look.space2
                    Item { Layout.fillWidth: true }
                    PillButton {
                        visible: !card.pk.installed && card.pk.hasDownload && card.pk.state !== "downloading" && card.pk.state !== "verifying"
                        enabled: card.accepted
                        variant: "primary"; glyph: "download"
                        text: card.pk.resumable ? qsTr("Resume") : card.pk.state === "failed" ? qsTr("Try again") : qsTr("Download")
                        accessibleName: qsTr("Download %1").arg(card.pk.title)
                        onClicked: root.engine.download(card.pk.id)
                    }
                    PillButton {
                        visible: card.pk.installed && root.kind === "zim"
                        variant: "primary"; text: qsTr("Open")
                        accessibleName: qsTr("Open %1").arg(card.pk.title)
                        onClicked: root.openRequested(card.pk.id)
                    }
                    PillButton {
                        visible: card.pk.installed
                        text: qsTr("Check file"); glyph: "shield"
                        accessibleName: qsTr("Check %1 against its checksum").arg(card.pk.title)
                        onClicked: card.verifyText = root.engine.verify(card.pk.id)
                    }
                    PillButton {
                        visible: card.pk.installed || card.pk.resumable
                        text: card.confirming ? qsTr("Delete for good?") : qsTr("Delete")
                        glyph: "trash"
                        accessibleName: card.confirming ? qsTr("Confirm: delete %1").arg(card.pk.title) : qsTr("Delete %1").arg(card.pk.title)
                        onClicked: {
                            if (!card.confirming) { card.confirming = true; confirmTimer.restart() }
                            else { card.confirming = false; root.engine.remove(card.pk.id) }
                        }
                    }
                }
            }
        }
    }

    // import a file the person already has
    GlassPanel {
        Layout.fillWidth: true
        implicitHeight: importCol.implicitHeight + Look.space4 * 2
        ColumnLayout {
            id: importCol
            anchors { fill: parent; margins: Look.space4 }
            spacing: Look.space2
            TaosText { text: qsTr("Already have a file?"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
            TaosText {
                Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                text: root.kind === "map" ? qsTr("Type the path of a .pmtiles map file. It is copied into your packs folder. Add its checksum if the publisher gave one and it is checked first.")
                                          : qsTr("Type the path of a .zim file. It is copied into your packs folder. Add its checksum if the publisher gave one and it is checked first.")
            }
            GridLayout {
                Layout.fillWidth: true
                columns: root.compact ? 2 : 3
                columnSpacing: Look.space2
                rowSpacing: Look.space2
                TaosField { id: pathField; Layout.fillWidth: true; Layout.columnSpan: root.compact ? 2 : 1
                            placeholderText: qsTr("Path to the file"); accessibleName: qsTr("Path to a pack file"); onAccepted: importBtn.clicked() }
                TaosField { id: shaField; Layout.fillWidth: root.compact; Layout.preferredWidth: root.compact ? 100 : 200
                            placeholderText: qsTr("Checksum (optional)"); accessibleName: qsTr("SHA-256 checksum, optional") }
                TaosButton { id: importBtn; text: qsTr("Import"); variant: "primary"; enabled: pathField.text.trim() !== ""
                             onClicked: if (root.engine.importFile(pathField.text, shaField.text)) { pathField.text = ""; shaField.text = "" } }
            }
        }
    }
    TaosText {
        visible: root.engine.message !== ""
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: root.engine.message
        font.pixelSize: Look.body; color: Look.textSecondary
        Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
    }
    TaosText {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: qsTr("Packs are stored in %1. %2 free.").arg(root.engine.dir).arg(root.engine.freeText)
        font.pixelSize: Look.small; color: Look.textTertiary
    }
}
