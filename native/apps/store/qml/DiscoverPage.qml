// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Discover: the featured app, then shelves. Popular apps as large cards with banner art, compact two-column shelves
// ("Replace your subscriptions", web apps, models), agents and themes in the same visual language.
Item {
    id: page
    property var engine
    signal openDetail(string id)
    signal gotoSection(string section)
    readonly property var disc: visible && engine.revision >= 0 ? engine.discoverPage() : ({ hero: ({}), shelves: [] })
    readonly property var hero: disc.hero ? disc.hero : ({})
    readonly property bool hasHero: hero.id !== undefined
    readonly property var themes: visible && engine.revision >= 0 ? engine.themes.themes : []
    readonly property bool empty: !hasHero && disc.shelves.length === 0
    readonly property real gutter: width < 760 ? Look.space4 : Look.space5

    StableModel { id: shelvesStable; source: page.disc.shelves }
    StableModel { id: themesStable; source: page.themes.slice(0, 4) }

    C.ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: page.gutter
            width: scroll.availableWidth - page.gutter * 2
            spacing: Look.space5

            // ---- loading, in the shape of the page
            ColumnLayout {
                visible: page.empty && page.engine.loading
                objectName: "discoverSkeleton"
                Layout.fillWidth: true; Layout.topMargin: Look.space5; spacing: Look.space5
                Skeleton { Layout.fillWidth: true; Layout.preferredHeight: 260; radius: Look.radiusCard + 2 }
                Skeleton { Layout.preferredWidth: 220; Layout.preferredHeight: 24 }
                RowLayout {
                    Layout.fillWidth: true; spacing: Look.space4
                    Repeater { model: 4; delegate: Skeleton { Layout.fillWidth: true; Layout.preferredHeight: 260; radius: Look.radiusCard } }
                }
            }
            EmptyState {
                visible: page.empty && !page.engine.loading
                Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 80
                glyph: "inbox"
                title: qsTr("Nothing to show yet")
                detail: qsTr("The catalogue is empty. Add a website from Web apps, or connect this device to a controller and try again.")
            }

            // ---- featured
            Hero {
                visible: page.hasHero
                objectName: "hero"
                Layout.fillWidth: true; Layout.topMargin: Look.space5
                kicker: qsTr("Featured  ·  Editor's choice")
                name: page.hero.name ? page.hero.name : ""
                tagline: page.hero.tagline ? page.hero.tagline : (page.hero.description ? page.hero.description : "")
                letter: page.hero.letter ? page.hero.letter : ""
                iconUrl: page.hero.iconUrl ? page.hero.iconUrl : ""
                coverUrl: page.hero.coverUrl ? page.hero.coverUrl : ""
                implicitHeight: page.width < 760 ? 240 : 280
                actions: Component {
                    RowLayout {
                        spacing: Look.space2
                        GetButton { engine: page.engine; entry: page.hero; compact: false; onReview: page.openDetail(page.hero.id) }
                        PillButton { text: qsTr("Preview"); variant: "outline"; accessibleName: qsTr("Preview %1").arg(page.hero.name)
                                     onClicked: page.openDetail(page.hero.id) }
                    }
                }
                onOpen: page.openDetail(page.hero.id)
            }

            // ---- shelves
            Repeater {
                model: shelvesStable.value
                delegate: ColumnLayout {
                    id: shelf
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.topMargin: !page.hasHero && shelf.index === 0 ? Look.space5 : 0   // no hero above: keep the page's top margin
                    spacing: Look.space3
                    SectionHeader {
                        Layout.fillWidth: true
                        title: shelf.modelData.title
                        showAction: true
                        onAction: page.gotoSection(shelf.modelData.section)
                    }
                    CardGrid {
                        visible: shelf.modelData.style === "cards"
                        Layout.fillWidth: true; Layout.preferredHeight: visible ? implicitHeight : 0
                        engine: page.engine; items: visible ? shelf.modelData.items : []; oneRow: true; tag: "disc"
                        onOpenDetail: (id) => page.openDetail(id)
                    }
                    CompactGrid {
                        visible: shelf.modelData.style === "compact"
                        Layout.fillWidth: true; Layout.preferredHeight: visible ? implicitHeight : 0
                        engine: page.engine; items: visible ? shelf.modelData.items : []; tag: "disc"
                        onOpenDetail: (id) => page.openDetail(id)
                    }
                }
            }

            // ---- themes
            ColumnLayout {
                visible: page.themes.length > 0
                Layout.fillWidth: true
                spacing: Look.space3
                SectionHeader { Layout.fillWidth: true; title: qsTr("Themes"); onAction: page.gotoSection("themes") }
                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.min(4, Math.floor((scroll.availableWidth - page.gutter * 2 + Look.space4) / (230 + Look.space4))))
                    columnSpacing: Look.space4; rowSpacing: Look.space4
                    Repeater {
                        model: themesStable.value
                        delegate: C.ItemDelegate {
                            id: tc
                            required property var modelData
                            Layout.fillWidth: true; Layout.preferredWidth: 1
                            implicitHeight: 148
                            padding: 0; topPadding: 0; bottomPadding: 0
                            hoverEnabled: true
                            focusPolicy: Qt.StrongFocus
                            objectName: "disc-theme-" + modelData.id
                            Accessible.role: Accessible.Button
                            Accessible.name: modelData.name + (modelData.current ? ", " + qsTr("in use") : "")
                            Accessible.description: modelData.description
                            onClicked: page.gotoSection("themes")
                            Keys.onReturnPressed: page.gotoSection("themes")
                            background: Rectangle {
                                radius: Look.radiusCard
                                color: tc.hovered || tc.visualFocus ? Look.raised : Look.surface
                                border.width: tc.visualFocus ? 2 : 1
                                border.color: tc.visualFocus ? Look.focusRing : Look.hairline
                                Behavior on color { ColorAnimation { duration: Look.fast } }
                            }
                            contentItem: ColumnLayout {
                                spacing: Look.space2
                                // the theme's own colours (its data, not this window's tokens)
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 76
                                    Layout.margins: Look.space3; Layout.bottomMargin: 0
                                    radius: Look.radiusControl + 2
                                    color: tc.modelData.swatches[0]
                                    border.width: 1; border.color: Look.hairline
                                    Rectangle { x: 10; y: 10; width: parent.width * 0.42; height: parent.height - 20; radius: 6; color: tc.modelData.swatches[1] }
                                    Rectangle { x: parent.width * 0.42 + 22; y: 16; width: parent.width * 0.3; height: 8; radius: 4; color: tc.modelData.swatches[2] }
                                    Rectangle { x: parent.width * 0.42 + 22; y: 32; width: parent.width * 0.2; height: 6; radius: 3; color: tc.modelData.swatches[1] }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; Layout.bottomMargin: Look.space3
                                    TaosText { text: tc.modelData.name; Layout.fillWidth: true; elide: Text.ElideRight
                                               font.pixelSize: Look.title; font.weight: Font.DemiBold }
                                    Chip { visible: tc.modelData.current; text: qsTr("In use"); tone: "success"; glyph: "check" }
                                }
                            }
                        }
                    }
                }
            }
            Item { Layout.preferredHeight: Look.space5 }
        }
    }
}
