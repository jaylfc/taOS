// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The whole Kiwix library: search, language, category, paging. Nothing is hand picked; what Kiwix publishes is here.
ColumnLayout {
    id: page
    property bool narrow: false
    readonly property var catalog: Survival.catalog
    readonly property var engine: Survival.packs
    property string lang: "eng"
    property string category: ""
    property string reviewing: ""      // pack id whose licence panel is open
    spacing: Look.space3
    function run(start) { catalog.search(queryField.text, lang, category, start || 0) }
    Component.onCompleted: { catalog.loadFacets(); run(0) }
    Timer { id: debounce; interval: 350; onTriggered: page.run(0) }

    GridLayout {
        Layout.fillWidth: true
        columns: page.narrow ? 2 : 3
        columnSpacing: Look.space2; rowSpacing: Look.space2
        TaosField {
            id: queryField
            Layout.fillWidth: true; Layout.columnSpan: page.narrow ? 2 : 1
            leftPadding: 34
            placeholderText: qsTr("Search the Kiwix library")
            accessibleName: qsTr("Search the Kiwix library")
            onTextChanged: debounce.restart()
            Glyph { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary }
        }
        TaosCombo {
            id: langBox
            Layout.fillWidth: page.narrow; Layout.preferredWidth: page.narrow ? -1 : 200
            accessibleName: qsTr("Language")
            model: [{id: "", title: qsTr("Any language")}].concat(page.catalog.languages)
            textRole: "title"; valueRole: "id"
            currentIndex: { for (let i = 0; i < model.length; i++) if (model[i].id === page.lang) return i; return 0 }
            onActivated: { page.lang = currentValue; page.run(0) }
        }
        TaosCombo {
            Layout.fillWidth: page.narrow; Layout.preferredWidth: page.narrow ? -1 : 200
            accessibleName: qsTr("Category")
            model: [{id: "", title: qsTr("All categories")}].concat(page.catalog.categories)
            textRole: "title"; valueRole: "id"
            onActivated: { page.category = currentValue; page.run(0) }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        TaosText { Layout.fillWidth: true; font.pixelSize: Look.small; color: Look.textSecondary
                   text: page.catalog.loading ? qsTr("Searching...") : page.catalog.error !== "" ? "" : qsTr("%L1 packs. Showing %2 to %3.").arg(page.catalog.total).arg(page.catalog.start + 1).arg(page.catalog.start + page.catalog.results.length) }
        PillButton { compact: true; text: qsTr("Previous"); enabled: page.catalog.start > 0 && !page.catalog.loading; onClicked: page.run(Math.max(0, page.catalog.start - 30)) }
        PillButton { compact: true; text: qsTr("Next"); enabled: page.catalog.start + 30 < page.catalog.total && !page.catalog.loading; onClicked: page.run(page.catalog.start + 30) }
    }
    C.ScrollView {
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: Look.space2
            // loading skeletons shaped like the cards
            Repeater { model: page.catalog.loading && page.catalog.results.length === 0 ? 4 : 0
                       delegate: Skeleton { Layout.fillWidth: true; Layout.preferredHeight: 92; radius: Look.radiusCard } }
            EmptyState {
                visible: page.catalog.error !== ""
                Layout.fillWidth: true; Layout.topMargin: Look.space5
                glyph: "alert"; tone: "error"; title: qsTr("The library could not be reached")
                detail: page.catalog.error + " " + qsTr("Packs you already have keep working offline.")
            }
            PillButton { visible: page.catalog.error !== ""; Layout.alignment: Qt.AlignHCenter; text: qsTr("Try again"); onClicked: page.run(page.catalog.start) }
            EmptyState {
                visible: !page.catalog.loading && page.catalog.error === "" && page.catalog.results.length === 0
                Layout.fillWidth: true; Layout.topMargin: Look.space5
                glyph: "search"; title: qsTr("Nothing matches"); detail: qsTr("Try fewer words, or another language or category.")
            }
            Repeater {
                model: page.catalog.results
                delegate: GlassPanel {
                    id: card
                    required property var modelData
                    readonly property bool busy: modelData.state === "downloading" || modelData.state === "verifying" || modelData.state === "preparing"
                    property bool accepted: false
                    Layout.fillWidth: true
                    implicitHeight: cardCol.implicitHeight + Look.space3 * 2
                    objectName: "result-" + modelData.id
                    ColumnLayout {
                        id: cardCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout {
                            Layout.fillWidth: true; spacing: Look.space3
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                TaosText { text: card.modelData.title; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.title; font.weight: Font.DemiBold }
                                TaosText { visible: card.modelData.description !== ""; text: card.modelData.description; Layout.fillWidth: true; wrapMode: Text.Wrap
                                           maximumLineCount: 2; elide: Text.ElideRight; font.pixelSize: Look.body; color: Look.textSecondary }
                            }
                            PillButton {
                                visible: !card.modelData.installed && !card.busy
                                variant: "primary"; glyph: card.modelData.restrictive ? "shield" : "download"
                                text: card.modelData.restrictive ? qsTr("Review licence") : qsTr("Download")
                                accessibleName: qsTr("Download %1").arg(card.modelData.title)
                                onClicked: { if (card.modelData.restrictive) page.reviewing = page.reviewing === card.modelData.id ? "" : card.modelData.id
                                             else page.catalog.install(card.modelData.id) }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: Look.space2
                            Chip { text: card.modelData.sizeText; glyph: "download" }
                            Chip { text: card.modelData.language.toUpperCase() }
                            Chip { visible: card.modelData.category !== ""; text: card.modelData.category }
                            Chip { visible: card.modelData.articlesText !== ""; text: qsTr("%1 articles").arg(card.modelData.articlesText) }
                            Chip { visible: card.modelData.pictures; text: qsTr("Pictures") }
                            Chip { visible: card.modelData.fullText; text: qsTr("Full text search"); tone: "success" }
                            Chip { text: card.modelData.licence; glyph: "info"; tone: card.modelData.restrictive ? "warning" : "neutral" }
                            Chip { visible: card.modelData.version !== ""; text: card.modelData.version; mono: true }
                            Chip { visible: card.modelData.installed; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                        }
                        TaosText { text: qsTr("From %1").arg(card.modelData.source); font.pixelSize: Look.caption; color: Look.textTertiary }
                        ColumnLayout {
                            visible: page.reviewing === card.modelData.id && !card.modelData.installed
                            Layout.fillWidth: true; spacing: Look.space2
                            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                                       text: card.modelData.licenceUrl !== "" ? qsTr("This pack is under %1. Read it at %2. It restricts use (for example to non-commercial use or no changes).").arg(card.modelData.licence).arg(card.modelData.licenceUrl)
                                                                              : qsTr("The licence of this pack is not known from the catalogue. It may restrict use. The pack carries its own licence, shown after it is installed.") }
                            TaosCheck { text: qsTr("I accept this licence"); checked: card.accepted; onToggled: card.accepted = checked }
                            PillButton { text: qsTr("Download"); variant: "primary"; glyph: "download"; enabled: card.accepted
                                         onClicked: { page.catalog.install(card.modelData.id); page.reviewing = "" } }
                        }
                        ColumnLayout {
                            visible: card.busy
                            Layout.fillWidth: true; spacing: 4
                            ProgressTrack { Layout.fillWidth: true; value: card.modelData.progress; indeterminate: card.modelData.state !== "downloading" }
                            TaosText { font.pixelSize: Look.small; color: Look.textSecondary
                                       text: card.modelData.state === "preparing" ? qsTr("Asking the publisher for the checksum") : card.modelData.state === "verifying" ? qsTr("Checking the file") : qsTr("%1 of %2").arg(card.modelData.receivedText || "0 B").arg(card.modelData.sizeText) }
                        }
                        TaosText { visible: card.modelData.error !== ""; text: card.modelData.error; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.danger }
                    }
                }
            }
        }
    }
    TaosText { visible: page.engine.message !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.engine.message; font.pixelSize: Look.body; color: Look.textSecondary }
}
