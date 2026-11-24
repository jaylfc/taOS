// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A section of the catalogue: a title, category chips and the entries as cards (apps, agents) or compact rows
// (models, plugins, tool servers, updates, search results). "Show more" reveals the rest in steps.
Item {
    id: page
    StableModel { id: chipsStable; source: page.chips }
    property var engine
    property string section: "apps"
    property string title: ""
    property string blurb: ""
    property string query: ""
    property string chip: "all"
    property string layoutKind: section === "apps" || section === "agents" ? "cards" : "compact"
    property int step: 48
    property int limit: step
    signal openDetail(string id)
    readonly property var chips: visible && engine.revision >= 0 ? engine.chips(section) : []
    readonly property var rows: visible && engine.revision >= 0 ? engine.list(section, query, chip) : []
    readonly property real gutter: width < 760 ? Look.space4 : Look.space5
    onSectionChanged: { chip = "all"; limit = step }
    onChipChanged: limit = step
    onQueryChanged: limit = step

    C.ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: page.gutter
            width: scroll.availableWidth - page.gutter * 2
            spacing: Look.space4

            ColumnLayout {
                Layout.fillWidth: true; Layout.topMargin: Look.space5
                spacing: Look.space2
                RowLayout {
                    spacing: Look.space2
                    TaosText { text: page.title; font.pixelSize: Look.pageTitle; font.weight: Font.Bold
                               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                    TaosText {
                        visible: page.rows.length > 0
                        text: qsTr("%1 shown").arg(page.rows.length)
                        color: Look.textSecondary; font.pixelSize: Look.body; font.features: { "tnum": 1 }
                        Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 3
                    }
                }
                TaosText {
                    visible: page.blurb !== ""
                    Layout.fillWidth: true; text: page.blurb; wrapMode: Text.Wrap
                    color: Look.textSecondary; font.pixelSize: Look.body
                }
                Flow {
                    Layout.fillWidth: true; Layout.topMargin: Look.space1
                    spacing: Look.space2
                    visible: page.chips.length > 2
                    Repeater {
                        model: chipsStable.value
                        delegate: Pill {
                            required property var modelData
                            text: modelData.label + "  " + modelData.count
                            checked: page.chip === modelData.id
                            onClicked: page.chip = modelData.id
                        }
                    }
                }
            }

            CardGrid {
                visible: page.layoutKind === "cards" && page.rows.length > 0
                objectName: "list-" + page.section
                Layout.fillWidth: true; Layout.preferredHeight: visible ? implicitHeight : 0
                engine: page.engine; items: visible ? page.rows : []; maxItems: page.limit; tag: "row"
                onOpenDetail: (id) => page.openDetail(id)
            }
            CompactGrid {
                visible: page.layoutKind === "compact" && page.rows.length > 0
                objectName: visible ? "list-" + page.section : ""
                Layout.fillWidth: true; Layout.preferredHeight: visible ? implicitHeight : 0
                engine: page.engine; items: visible ? page.rows : []; maxItems: page.limit; tag: "row"
                onOpenDetail: (id) => page.openDetail(id)
            }
            PillButton {
                visible: page.rows.length > page.limit
                Layout.alignment: Qt.AlignHCenter
                text: qsTr("Show %1 more").arg(Math.min(page.step, page.rows.length - page.limit))
                variant: "outline"
                onClicked: page.limit += page.step
            }

            // loading, in the shape of the page
            GridLayout {
                visible: page.rows.length === 0 && page.engine.loading
                Layout.fillWidth: true
                columns: page.layoutKind === "cards" ? 3 : 2
                columnSpacing: Look.space4; rowSpacing: Look.space4
                Repeater {
                    model: page.layoutKind === "cards" ? 6 : 8
                    delegate: Skeleton { Layout.fillWidth: true; Layout.preferredWidth: 1
                                         Layout.preferredHeight: page.layoutKind === "cards" ? 240 : 56; radius: Look.radiusCard }
                }
            }
            EmptyState {
                visible: page.rows.length === 0 && !page.engine.loading
                Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 60
                glyph: "inbox"
                title: page.query !== "" ? qsTr("Nothing matches") : qsTr("Nothing here")
                detail: page.query !== "" ? qsTr("No item here matches “%1”. Try fewer words.").arg(page.query)
                        : page.section === "updates" ? qsTr("Everything installed is up to date.")
                        : page.engine.errorText !== "" ? page.engine.errorText
                        : qsTr("This section is empty. Try again once the controller has answered.")
            }
            Item { Layout.preferredHeight: Look.space5 }
        }
    }
}
