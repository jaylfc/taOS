import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Settings 1.0
import Taos.Style 1.0

// Settings: the system control panel. A face over the engines (theme, display, window, dock, audio, network); it
// keeps no state of its own except where it is (section, search text).
TaosWindow {
    id: win
    width: 900
    height: 640
    minimumWidth: 640
    minimumHeight: 440
    title: qsTr("Settings")
    objectName: "settingsWindow"
    palette.window: TaosStyle.ground
    palette.windowText: TaosStyle.text
    palette.text: TaosStyle.text
    palette.base: TaosStyle.surface

    readonly property bool searching: Settings.nav.query.trim().length > 0

    Shortcut { sequences: [StandardKey.Find]; onActivated: { search.forceActiveFocus(); search.selectAll() } }

    function focusContent() {
        const it = searching ? results : (pageLoader.item ? pageLoader.item : null)
        if (!it) return
        it.forceActiveFocus()
        const n = it.nextItemInFocusChain(true)
        if (n && n !== it && !searching) n.forceActiveFocus()
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---- sidebar -------------------------------------------------------------------------
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 244
            color: TaosStyle.surface
            Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: 1; color: TaosStyle.hairline }

            ColumnLayout {
                anchors { fill: parent; topMargin: 14; bottomMargin: 10; leftMargin: 12; rightMargin: 13 }
                spacing: 10

                C.TextField {
                    id: search
                    Layout.fillWidth: true
                    Layout.preferredHeight: 32
                    placeholderText: qsTr("Search")
                    text: Settings.nav.query
                    onTextChanged: if (text !== Settings.nav.query) Settings.nav.query = text
                    leftPadding: 32
                    color: TaosStyle.text
                    placeholderTextColor: TaosStyle.textTertiary
                    selectionColor: TaosStyle.selection
                    selectedTextColor: TaosStyle.text
                    font.family: "Inter"
                    font.pixelSize: TaosStyle.bodySize
                    Accessible.name: qsTr("Search settings")
                    Accessible.role: Accessible.EditableText
                    background: Rectangle {
                        radius: 8
                        color: TaosStyle.track
                        border.width: search.activeFocus ? 2 : 1
                        border.color: search.activeFocus ? TaosStyle.focusRing : TaosStyle.hairline
                        Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 14 }
                    }
                    Connections { target: Settings.nav; function onQueryChanged() { if (search.text !== Settings.nav.query) search.text = Settings.nav.query } }
                    Keys.onEscapePressed: { text = ""; list.forceActiveFocus() }
                    Keys.onDownPressed: if (win.searching) results.forceActiveFocus(); else list.forceActiveFocus()
                    Keys.onReturnPressed: if (win.searching) { results.openCurrent(); list.forceActiveFocus() }
                }

                ListView {
                    id: list
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: Settings.catalog.sections
                    clip: true
                    spacing: 2
                    keyNavigationEnabled: true
                    activeFocusOnTab: true
                    currentIndex: { for (let i = 0; i < count; ++i) if (Settings.catalog.sections[i].id === Settings.nav.section) return i; return 0 }
                    onCurrentIndexChanged: if (activeFocus && currentIndex >= 0) Settings.nav.navigate(Settings.catalog.sections[currentIndex].id)
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("Settings sections")
                    Keys.onRightPressed: win.focusContent()
                    Keys.onReturnPressed: win.focusContent()
                    delegate: Item {
                        id: del
                        required property var modelData
                        required property int index
                        readonly property bool current: Settings.nav.section === modelData.id && !win.searching
                        width: ListView.view.width
                        height: 36
                        Accessible.role: Accessible.ListItem
                        Accessible.name: modelData.title
                        Accessible.selected: current
                        Rectangle {
                            anchors.fill: parent; radius: 8
                            color: del.current ? TaosStyle.selection : (hover.hovered ? TaosStyle.raised : "transparent")
                            border.width: del.ListView.isCurrentItem && list.activeFocus ? 2 : 0
                            border.color: TaosStyle.focusRing
                            Behavior on color { ColorAnimation { duration: TaosStyle.reduceMotion ? 0 : 150 } }
                        }
                        RowLayout {
                            anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                            spacing: 10
                            Icon { name: del.modelData.icon; size: 18; color: del.current ? TaosStyle.accent : TaosStyle.textSecondary }
                            Txt { Layout.fillWidth: true; text: del.modelData.title; wrapMode: Text.NoWrap; elide: Text.ElideRight; font.weight: del.current ? Font.DemiBold : Font.Normal; Accessible.ignored: true }
                        }
                        HoverHandler { id: hover }
                        TapHandler { onTapped: { list.currentIndex = del.index; Settings.nav.navigate(del.modelData.id) } }
                    }
                }
            }
        }

        // ---- content --------------------------------------------------------------------------
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Loader {
                id: pageLoader
                anchors.fill: parent
                visible: !win.searching
                active: !win.searching
                source: Settings.catalog.component(Settings.nav.section)
                focus: false
            }
            Connections { target: pageLoader.item; ignoreUnknownSignals: true; function onLeave() { list.forceActiveFocus() } }
            SearchResults {
                id: results
                anchors.fill: parent
                visible: win.searching
                query: Settings.nav.query.trim()
            }
        }
    }
    onActiveChanged: if (active) Settings.about.refresh()
}
