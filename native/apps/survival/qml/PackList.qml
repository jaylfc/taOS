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
    readonly property var list: { engine.packs; return engine.ofKind(kind) }
    spacing: Look.space3

    Repeater {
        model: root.list
        delegate: GlassPanel {
            id: card
            required property var modelData
            property bool confirming: false
            property string verifyText: ""
            property bool accepted: modelData.licenceAccepted || !modelData.needsAcceptance
            Layout.fillWidth: true
            implicitHeight: body.implicitHeight + Look.space4 * 2
            objectName: "pack-" + modelData.id
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
                        width: 40; height: 40; radius: 11
                        color: Look.field; border.width: 1; border.color: Look.hairline
                        Glyph { anchors.centerIn: parent; name: root.kind === "map" ? "map" : "book"; size: 20; stroke: 1.8; color: Look.textSecondary }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        TaosText { text: card.modelData.title; Layout.fillWidth: true; wrapMode: Text.Wrap
                                   font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                        TaosText { visible: card.modelData.description !== ""; text: card.modelData.description; Layout.fillWidth: true
                                   wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                        Flow {
                            Layout.fillWidth: true
                            Layout.topMargin: 4
                            spacing: Look.space2
                            Chip { visible: card.modelData.installed; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                            Chip { visible: card.modelData.sizeText !== "" && !card.modelData.installed; text: card.modelData.sizeText; glyph: "download" }
                            Chip { visible: card.modelData.installed && card.modelData.installedText !== ""; text: card.modelData.installedText; glyph: "hard-drive" }
                            Chip { visible: card.modelData.licence !== ""; text: card.modelData.licence; glyph: "info"
                                   accessibleName: qsTr("Licence %1").arg(card.modelData.licence) }
                            Chip { visible: card.modelData.language !== ""; text: card.modelData.language.toUpperCase() }
                            Chip { visible: card.modelData.local; text: qsTr("Your file") }
                        }
                    }
                }
                // not installed, no publisher download: how to make it
                ColumnLayout {
                    visible: !card.modelData.installed && !card.modelData.hasDownload && card.modelData.recipe !== ""
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
                            text: card.modelData.recipe
                            font.family: "monospace"; font.pixelSize: Look.small; color: Look.textSecondary
                            selectionColor: Look.selection
                            Accessible.role: Accessible.StaticText
                            Accessible.name: qsTr("Command to make a map pack")
                        }
                    }
                }
                // downloading
                ColumnLayout {
                    visible: card.modelData.state === "downloading" || card.modelData.state === "verifying"
                    Layout.fillWidth: true
                    spacing: 6
                    ProgressTrack { Layout.fillWidth: true; value: card.modelData.progress; indeterminate: card.modelData.state === "verifying" }
                    RowLayout {
                        Layout.fillWidth: true
                        TaosText {
                            Layout.fillWidth: true
                            font.pixelSize: Look.small; color: Look.textSecondary
                            text: card.modelData.state === "verifying" ? qsTr("Checking the file against its checksum")
                                : qsTr("%1 of %2").arg(card.modelData.receivedText || "0 B").arg(card.modelData.sizeText)
                        }
                        TaosButton { visible: card.modelData.state === "downloading"; text: qsTr("Pause"); compact: true
                                     accessibleName: qsTr("Pause the download of %1").arg(card.modelData.title)
                                     onClicked: root.engine.cancel(card.modelData.id) }
                    }
                }
                // failed
                RowLayout {
                    visible: card.modelData.error !== ""
                    Layout.fillWidth: true
                    spacing: Look.space2
                    Glyph { name: "alert"; size: 16; stroke: 2; color: Look.danger; Layout.alignment: Qt.AlignTop }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: card.modelData.error; font.pixelSize: Look.body; color: Look.danger }
                }
                // licence acceptance
                TaosCheck {
                    visible: card.modelData.needsAcceptance && !card.modelData.installed
                    text: qsTr("I accept the %1 licence").arg(card.modelData.licence)
                    checked: card.accepted
                    onToggled: if (checked) root.engine.acceptLicence(card.modelData.id)
                }
                TaosText { visible: card.verifyText !== ""; text: card.verifyText; Layout.fillWidth: true; wrapMode: Text.Wrap
                           font.pixelSize: Look.body; color: Look.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Look.space2
                    TaosText {
                        Layout.fillWidth: true
                        visible: card.modelData.attribution !== ""
                        text: card.modelData.attribution
                        wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                    }
                    Item { Layout.fillWidth: true; visible: card.modelData.attribution === "" }
                    PillButton {
                        visible: !card.modelData.installed && card.modelData.hasDownload && card.modelData.state !== "downloading" && card.modelData.state !== "verifying"
                        enabled: card.accepted
                        variant: "primary"; glyph: "download"
                        text: card.modelData.resumable ? qsTr("Resume") : card.modelData.state === "failed" ? qsTr("Try again") : qsTr("Download")
                        accessibleName: qsTr("Download %1").arg(card.modelData.title)
                        onClicked: root.engine.download(card.modelData.id)
                    }
                    PillButton {
                        visible: card.modelData.installed && root.kind === "zim"
                        variant: "primary"; text: qsTr("Open")
                        accessibleName: qsTr("Open %1").arg(card.modelData.title)
                        onClicked: root.openRequested(card.modelData.id)
                    }
                    PillButton {
                        visible: card.modelData.installed
                        text: qsTr("Check file"); glyph: "shield"
                        accessibleName: qsTr("Check %1 against its checksum").arg(card.modelData.title)
                        onClicked: card.verifyText = root.engine.verify(card.modelData.id)
                    }
                    PillButton {
                        visible: card.modelData.installed || card.modelData.resumable
                        text: card.confirming ? qsTr("Delete for good?") : qsTr("Delete")
                        glyph: "trash"
                        accessibleName: card.confirming ? qsTr("Confirm: delete %1").arg(card.modelData.title) : qsTr("Delete %1").arg(card.modelData.title)
                        onClicked: {
                            if (!card.confirming) { card.confirming = true; confirmTimer.restart() }
                            else { card.confirming = false; root.engine.remove(card.modelData.id) }
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
            RowLayout {
                Layout.fillWidth: true
                spacing: Look.space2
                TaosField { id: pathField; Layout.fillWidth: true; placeholderText: qsTr("Path to the file"); accessibleName: qsTr("Path to a pack file")
                            onAccepted: importBtn.clicked() }
                TaosField { id: shaField; Layout.preferredWidth: root.compact ? 120 : 200; placeholderText: qsTr("Checksum (optional)"); accessibleName: qsTr("SHA-256 checksum, optional") }
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
