// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Style 1.0
import Taos.Apps 1.0
import Taos.Survival 1.0

// Survival Studio: offline knowledge, offline maps, awareness and kits. Desktop: a sidebar, the section and the agent
// panel on the right. Phone width: a tab bar at the bottom and one section at a time. Everything works with no
// network; the only thing that ever goes online is a pack download the person asks for.
TaosWindow {
    id: win
    width: Math.min(1500, Math.max(900, Screen.width - 80))
    height: Math.min(950, Math.max(600, Screen.height - 200))
    minimumWidth: 360
    minimumHeight: 520
    title: qsTr("Survival Studio")
    objectName: "survivalWindow"
    property string section: "library"
    property bool agentOpen: true
    property bool agentPopped: false
    readonly property bool narrow: width < 760
    readonly property var sections: [
        {id: "library", text: qsTr("Library"), glyph: "book"},
        {id: "map", text: qsTr("Map"), glyph: "map"},
        {id: "taostak", text: qsTr("taOStak"), glyph: "radio"},
        {id: "kit", text: qsTr("Kit"), glyph: "backpack"}]
    readonly property var phoneTabs: sections.concat([{id: "agent", text: qsTr("Agent"), glyph: "sparkles"}])
    function go(id) { section = id }
    function argValue(args, name) {
        for (let i = 0; i < args.length; i++) {
            if (args[i] === name && i + 1 < args.length) return args[i + 1]
            if (args[i].indexOf(name + "=") === 0) return args[i].substring(name.length + 1)
        }
        return ""
    }
    function applyArgs(args) {
        const s = argValue(args, "--section")
        if (s !== "") section = s
        if (args.indexOf("--no-agent") >= 0) agentOpen = false
        const q = argValue(args, "--search")
        if (q !== "") { section = "library"; Qt.callLater(() => libraryPage.runSearch(q)) }
        const vw = argValue(args, "--view")
        if (vw !== "") { const v = vw.split(","); Qt.callLater(() => (section === "taostak" ? takPage : mapPage).stage.view.setView(Number(v[0]), Number(v[1]), Number(v[2]))) }
        const mq = argValue(args, "--map-search")
        if (mq !== "") { Qt.callLater(() => (section === "taostak" ? takPage : mapPage).stage.setSearch(mq)) }
        const tab = argValue(args, "--tab")
        if (tab !== "") {
            if (section === "library") libraryPage.mode = tab
            else if (section === "map") mapPage.tab = tab
            else if (section === "taostak") takPage.tab = tab
        }
        const ptab = argValue(args, "--pack-tab")
        if (ptab !== "") { section = "library"; libraryPage.mode = "packs"; libraryPage.packTab = ptab }
        const gk = argValue(args, "--gear-kit")
        if (gk !== "") { section = "kit"; Qt.callLater(() => { Survival.gear.selectByName(gk)
            const gc = argValue(args, "--gear-check")
            if (gc !== "") { Survival.gear.tab = "check"; Survival.gear.runAudit(gc) } }) }
        const art = argValue(args, "--article")
        if (art !== "") { section = "library"; if (Survival.library.packId === "" && libraryPage.installed.length > 0) Survival.library.open(libraryPage.installed[0].id); Survival.library.openArticle(art); libraryPage.mode = "read"; libraryPage.reading = true }
        const pk = argValue(args, "--open-pack")
        if (pk !== "") libraryPage.openPack(pk)
    }
    Component.onCompleted: applyArgs(Taos.arguments)
    Connections {
        target: Taos
        function onActivated(args) { win.applyArgs(args); win.raise(); win.requestActivate() }
    }
    Shortcut { sequence: "Ctrl+1"; onActivated: win.go("library") }
    Shortcut { sequence: "Ctrl+2"; onActivated: win.go("map") }
    Shortcut { sequence: "Ctrl+3"; onActivated: win.go("taostak") }
    Shortcut { sequence: "Ctrl+4"; onActivated: win.go("kit") }
    Shortcut { sequences: ["Ctrl+F", "/"]; onActivated: stack.itemAt(stack.currentIndex).focusSearch() }
    Shortcut { sequence: "Ctrl+Shift+A"; onActivated: win.agentOpen = !win.agentOpen }

    // ---- sidebar (wide)
    Item {
        id: sidebar
        visible: !win.narrow
        width: visible ? 232 : 0
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: 1; color: Look.hairline }
        ColumnLayout {
            anchors { fill: parent; leftMargin: Look.space3; rightMargin: Look.space3; topMargin: Look.space4; bottomMargin: Look.space3 }
            spacing: Look.space3
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: Look.space2
                spacing: Look.space3
                Rectangle {
                    width: 32; height: 32; radius: Look.radiusCard - 5
                    color: Look.tint(Look.accent, 0.16); border.width: 1; border.color: Look.tint(Look.accent, 0.30)
                    Glyph { anchors.centerIn: parent; name: "compass"; size: 18; stroke: 1.8; color: Look.accent }
                }
                ColumnLayout {
                    spacing: 0
                    TaosText { text: qsTr("Survival Studio"); font.pixelSize: Look.bodyLarge; font.weight: Font.DemiBold }
                    TaosText { text: qsTr("Works offline"); font.pixelSize: Look.caption; color: Look.textTertiary }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.topMargin: Look.space2
                spacing: 2
                Repeater {
                    model: win.sections
                    delegate: SidebarItem {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        objectName: "section-" + modelData.id
                        text: modelData.text
                        glyph: modelData.glyph
                        current: win.section === modelData.id
                        onClicked: win.go(modelData.id)
                        Keys.onDownPressed: nextItemInFocusChain(true).forceActiveFocus()
                        Keys.onUpPressed: nextItemInFocusChain(false).forceActiveFocus()
                    }
                }
            }
            Item { Layout.fillHeight: true }
            SidebarItem {
                Layout.fillWidth: true
                objectName: "agentToggle"
                text: qsTr("Agent")
                glyph: "sparkles"
                current: win.agentOpen && !win.agentPopped
                onClicked: { if (win.agentPopped) win.agentPopped = false; else win.agentOpen = !win.agentOpen }
            }
            GlassPanel {
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Look.radiusCard - 2
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("Packs folder, %1 free").arg(Survival.packs.freeText)
                RowLayout {
                    anchors { fill: parent; leftMargin: Look.space3; rightMargin: Look.space3 }
                    spacing: Look.space3
                    Glyph { name: "hard-drive"; size: 18; stroke: 1.8; color: Look.textSecondary }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 0
                        TaosText { text: qsTr("Stored here"); font.pixelSize: Look.body; font.weight: Font.DemiBold }
                        TaosText { text: qsTr("%1 free").arg(Survival.packs.freeText); Layout.fillWidth: true; elide: Text.ElideRight
                                   font.pixelSize: Look.small; color: Look.textSecondary }
                    }
                }
            }
        }
    }

    // ---- the section
    StackLayout {
        id: stack
        anchors {
            left: sidebar.right; top: parent.top; bottom: win.narrow ? tabs.top : parent.bottom
            right: agentColumn.visible ? agentColumn.left : parent.right
        }
        currentIndex: Math.max(0, win.sections.findIndex(s => s.id === win.section))
        LibraryPage { id: libraryPage; narrow: win.narrow }
        MapPage { id: mapPage; narrow: win.narrow }
        TakPage { id: takPage; narrow: win.narrow }
        KitPage { narrow: win.narrow }
    }

    // ---- the agent (right on desktop, its own tab on a phone)
    Item {
        id: agentColumn
        visible: win.narrow ? win.section === "agent" : (win.agentOpen && !win.agentPopped)
        width: win.narrow ? win.width : 320
        anchors { right: parent.right; top: parent.top; bottom: win.narrow ? tabs.top : parent.bottom }
        z: 2
        Rectangle { anchors.fill: parent; color: Look.ground }
        Rectangle { anchors { left: parent.left; top: parent.top; bottom: parent.bottom } width: win.narrow ? 0 : 1; color: Look.hairline }
        StudioAgentPanel {
            anchors.fill: parent
            studio: qsTr("Survival Studio")
            examples: [qsTr("Answer from the packs you installed, naming the pack and the article."),
                       qsTr("Suggest map markers and routes for you to approve."),
                       qsTr("Draft a message for the mesh. You send it."),
                       qsTr("Tell you what is missing from a kit.")]
            onPopOutRequested: { win.agentPopped = true }
        }
    }
    TaosWindow {
        id: agentWindow
        visible: win.agentPopped
        width: 380; height: 620
        minimumWidth: 300; minimumHeight: 400
        title: qsTr("Agent - Survival Studio")
        objectName: "agentWindow"
        onClosing: win.agentPopped = false
        StudioAgentPanel {
            anchors.fill: parent
            studio: qsTr("Survival Studio")
            poppedOut: true
            examples: [qsTr("Answer from the packs you installed, naming the pack and the article."),
                       qsTr("Suggest map markers and routes for you to approve."),
                       qsTr("Draft a message for the mesh. You send it."),
                       qsTr("Tell you what is missing from a kit.")]
            onPopOutRequested: win.agentPopped = false
        }
    }

    // ---- tab bar (phone)
    BottomTabBar {
        id: tabs
        objectName: "phoneTabs"
        visible: win.narrow
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        model: win.phoneTabs
        currentId: win.section
        onActivated: (id) => win.section = id
    }
}
