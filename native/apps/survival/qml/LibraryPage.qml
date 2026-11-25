// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Library: offline knowledge from ZIM packs. Read (search and the reader) and Packs (download, import, delete).
Item {
    id: page
    objectName: "page-library"
    property bool narrow: false
    property string mode: "read"
    property bool reading: false
    property string packTab: "installed"
    readonly property var lib: Survival.library
    readonly property var engine: Survival.packs
    readonly property var installed: { engine.packs; return engine.ofKind("zim", true) }
    readonly property bool hasPack: lib.packId !== ""
    function focusSearch() { mode = "read"; reading = false; searchField.forceActiveFocus() }
    function runSearch(q) { mode = "read"; reading = false; searchField.text = q; lib.search(q) }
    function openPack(id) { if (lib.open(id)) { mode = "read"; reading = page.narrow } }

    Component.onCompleted: if (!hasPack && installed.length > 0) lib.open(installed[0].id)
    Connections {
        target: page.engine
        function onPackInstalled(id) { if (!page.hasPack && page.engine.pack(id).kind === "zim") page.lib.open(id) }
    }
    Connections {
        target: page.lib
        function onArticleChanged() { if (page.narrow) page.reading = true }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        // header
        Item {
            Layout.fillWidth: true
            Layout.margins: page.narrow ? Look.space4 : Look.space5
            Layout.bottomMargin: Look.space3
            implicitHeight: Math.max(titleCol.implicitHeight, seg.implicitHeight)
            visible: !(page.narrow && page.reading && page.mode === "read")
            ColumnLayout {
                id: titleCol
                anchors { left: parent.left; right: seg.left; rightMargin: Look.space3; verticalCenter: parent.verticalCenter }
                spacing: 2
                TaosText { text: qsTr("Library"); font.pixelSize: Look.pageTitle; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText { visible: !page.narrow; Layout.fillWidth: true; elide: Text.ElideRight
                           text: qsTr("Encyclopedias, manuals and guides that work with no internet.")
                           font.pixelSize: Look.body; color: Look.textSecondary }
            }
            Segmented {
                id: seg
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                accessibleName: qsTr("Library view")
                model: [{id: "read", text: qsTr("Read"), glyph: "book"}, {id: "packs", text: qsTr("Packs"), glyph: "download"}]
                currentId: page.mode
                onActivated: (id) => { page.mode = id; page.reading = false }
            }
        }
        // packs: everything that can be downloaded
        PackBrowser {
            visible: page.mode === "packs"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: page.narrow ? Look.space4 : Look.space5
            Layout.rightMargin: page.narrow ? Look.space4 : Look.space5
            narrow: page.narrow
            tab: page.packTab
            onTabChanged: page.packTab = tab
            onOpenRequested: (id) => page.openPack(id)
        }
        // nothing installed
        Item {
            visible: page.mode === "read" && page.installed.length === 0
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - Look.space6, 420)
                spacing: Look.space4
                EmptyState {
                    Layout.fillWidth: true
                    glyph: "book"
                    title: qsTr("Your library is empty")
                    detail: qsTr("Download a pack and it works offline from then on. A small one such as Wikipedia medicine is a good start.")
                }
                PillButton { Layout.alignment: Qt.AlignHCenter; variant: "primary"; text: qsTr("Get a library pack"); glyph: "download"
                             onClicked: page.mode = "packs" }
            }
        }
        // read
        RowLayout {
            visible: page.mode === "read" && page.installed.length > 0
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 0
            // list column
            ColumnLayout {
                visible: !page.narrow || !page.reading
                Layout.preferredWidth: page.narrow ? -1 : 340
                Layout.fillWidth: page.narrow
                Layout.fillHeight: true
                Layout.leftMargin: page.narrow ? Look.space4 : Look.space5
                Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                spacing: Look.space3
                TaosCombo {
                    id: packPicker
                    visible: page.installed.length > 1
                    Layout.fillWidth: true
                    accessibleName: qsTr("Pack to read")
                    model: page.installed
                    textRole: "title"
                    valueRole: "id"
                    currentIndex: { for (let i = 0; i < page.installed.length; i++) if (page.installed[i].id === page.lib.packId) return i; return -1 }
                    onActivated: page.lib.open(currentValue)
                }
                TaosField {
                    id: searchField
                    objectName: "librarySearch"
                    Layout.fillWidth: true
                    implicitHeight: 36
                    leftPadding: 34
                    placeholderText: qsTr("Search this pack")
                    accessibleName: qsTr("Search the library")
                    enabled: page.hasPack
                    onTextChanged: searchDelay.restart()
                    Keys.onDownPressed: if (resultList.count > 0) { resultList.forceActiveFocus(); resultList.currentIndex = 0 }
                    Keys.onEscapePressed: text = ""
                    Glyph { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary }
                    Timer { id: searchDelay; interval: 180; onTriggered: page.lib.search(searchField.text) }
                }
                // results
                ListView {
                    id: resultList
                    objectName: "libraryResults"
                    visible: searchField.text.trim() !== ""
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true
                    spacing: 2
                    model: page.lib.results
                    boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: C.ScrollBar { }
                    delegate: ListRow {
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        title: modelData.title
                        subtitle: modelData.snippet !== modelData.title ? modelData.snippet : ""
                        onClicked: page.lib.openArticle(modelData.path)
                        Keys.onUpPressed: if (index === 0) searchField.forceActiveFocus(); else ListView.view.decrementCurrentIndex()
                        Keys.onDownPressed: ListView.view.incrementCurrentIndex()
                    }
                    EmptyState {
                        visible: resultList.count === 0 && page.lib.query.trim() !== ""
                        anchors.centerIn: parent
                        glyph: "search"; title: qsTr("Nothing found"); detail: qsTr("No article title in this pack matches \"%1\".").arg(page.lib.query)
                    }
                }
                // the pack, when not searching
                ColumnLayout {
                    visible: searchField.text.trim() === ""
                    Layout.fillWidth: true; Layout.fillHeight: true
                    spacing: Look.space3
                    GlassPanel {
                        Layout.fillWidth: true
                        implicitHeight: infoCol.implicitHeight + Look.space4 * 2
                        ColumnLayout {
                            id: infoCol
                            anchors { fill: parent; margins: Look.space4 }
                            spacing: Look.space2
                            TaosText { text: page.lib.info.title || ""; Layout.fillWidth: true; wrapMode: Text.Wrap
                                       font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                            TaosText { visible: !!page.lib.info.description; text: page.lib.info.description || ""; Layout.fillWidth: true
                                       wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                            Flow {
                                Layout.fillWidth: true; spacing: Look.space2
                                Chip { text: qsTr("%L1 articles").arg(page.lib.info.articles || 0); glyph: "book" }
                                Chip { visible: !!page.lib.info.fullText; text: qsTr("Full text search"); tone: "success" }
                                Chip { visible: !!page.lib.info.licence; text: page.lib.info.licence || "" }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: Look.space2
                        PillButton { Layout.fillWidth: true; text: qsTr("Start page"); glyph: "home"; onClicked: page.lib.home() }
                        PillButton { Layout.fillWidth: true; text: qsTr("Random"); glyph: "shuffle"; onClicked: page.lib.random() }
                    }
                    Item { Layout.fillHeight: true }
                }
            }
            Rectangle { visible: !page.narrow; Layout.fillHeight: true; width: 1; color: Look.hairline }
            ArticleView {
                visible: !page.narrow || page.reading
                Layout.fillWidth: true; Layout.fillHeight: true
                narrow: page.narrow
                onBackToList: page.reading = false
            }
        }
    }
}
