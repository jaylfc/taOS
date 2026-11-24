// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Style 1.0
import Taos.Apps 1.0
import Taos.Store 1.0

// Store: apps, web apps, models, themes and plugins for this device. A face over the StoreEngine (catalogue, install
// state machine) and the web apps engine. Operate-mode UI: tokens only, words for every state, full keyboard use.
TaosWindow {
    id: win
    // 1500 x 950 where the screen has room; on a smaller one, leave space for the bar and the dock
    width: Math.min(1500, Math.max(900, Screen.width - 80))
    height: Math.min(950, Math.max(600, Screen.height - 200))
    minimumWidth: 520
    minimumHeight: 420
    title: qsTr("Store")
    objectName: "storeWindow"

    property string section: "discover"
    property string query: ""
    property string detailId: ""
    property string toastText: ""
    property alias engine: engine
    readonly property bool narrow: width < 760
    readonly property string shown: query !== "" && section === "discover" ? "search" : section
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    StoreEngine {
        id: engine
        objectName: "engine"
        session: Taos.session
        running: win.visible
        onToast: (text) => win.showToast(text)
    }

    function showToast(text) { toastText = text; toastTimer.restart() }
    Timer { id: toastTimer; interval: 4500; onTriggered: win.toastText = "" }

    function go(sec) { section = sec; detailId = ""; if (sec !== "discover") {} }
    function openApp(id) { detailId = id }
    function argValue(args, name) {
        for (let i = 0; i < args.length; i++) {
            if (args[i] === name && i + 1 < args.length) return args[i + 1]
            if (args[i].indexOf(name + "=") === 0) return args[i].substring(name.length + 1)
        }
        return ""
    }
    function applyArgs(args) {
        const s = argValue(args, "--section")
        if (s !== "") { section = s; detailId = "" }
        const a = argValue(args, "--app")
        if (a !== "") detailId = a
        if (args.indexOf("--add-website") >= 0) addDialog.open()
    }
    Component.onCompleted: applyArgs(Taos.arguments)
    Connections {
        target: Taos
        function onActivated(args) { win.applyArgs(args); win.raise(); win.requestActivate() }
    }

    Shortcut { sequences: ["Ctrl+F", "/"]; onActivated: search.forceActiveFocus() }
    Shortcut { sequences: ["Ctrl+R", "F5"]; onActivated: engine.refresh() }
    Shortcut { sequence: "Ctrl+N"; onActivated: addDialog.open() }
    Shortcut { sequence: "Escape"; enabled: win.detailId !== ""; onActivated: win.detailId = "" }

    readonly property var sectionDefs: engine.revision >= 0 ? engine.sections : []
    readonly property string deviceText: {
        const p = engine.profileId
        if (p === "") return engine.usingSnapshot ? qsTr("Not connected") : qsTr("Reading this device")
        const t = p.replace(/-/g, " ")
        return t.charAt(0).toUpperCase() + t.slice(1)
    }
    function countText(id) {
        if (engine.revision < 0) return ""
        if (id === "discover") return ""
        const n = engine.count(id)
        return n > 0 ? String(n) : ""
    }

    // ---- sidebar (wide) or a row of pills (narrow)
    Item {
        id: sidebar
        visible: !win.narrow
        width: visible ? 248 : 0
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: 1; color: Look.hairline }
        ColumnLayout {
            anchors { fill: parent; leftMargin: Look.space3; rightMargin: Look.space3; topMargin: Look.space4; bottomMargin: Look.space3 }
            spacing: Look.space3
            TaosField {
                id: search
                objectName: "searchField"
                Layout.fillWidth: true
                implicitHeight: 36
                leftPadding: 34
                placeholderText: qsTr("Search apps, agents, models")
                accessibleName: qsTr("Search the Store")
                text: win.query
                onTextChanged: { win.query = text; if (text !== "") win.detailId = "" }
                Keys.onEscapePressed: { text = ""; nextItemInFocusChain(true).forceActiveFocus() }
                Keys.onDownPressed: nextItemInFocusChain(true).forceActiveFocus()
                Glyph { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary }
            }
            C.ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                Column {
                    id: navCol
                    objectName: "sections"
                    width: parent.width
                    spacing: 2
                    Repeater {
                        model: win.sectionDefs
                        delegate: Column {
                            id: navEntry
                            required property var modelData
                            required property int index
                            width: navCol.width
                            spacing: 2
                            // a heading and a gap before the Library group
                            TaosText {
                                visible: navEntry.modelData.group === "library" && (navEntry.index === 0 || win.sectionDefs[navEntry.index - 1].group !== "library")
                                height: visible ? 34 : 0
                                width: parent.width
                                leftPadding: Look.space3
                                verticalAlignment: Text.AlignBottom
                                bottomPadding: Look.space1
                                text: qsTr("Library")
                                font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.textTertiary
                                font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                                Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                            }
                            SidebarItem {
                                width: parent.width
                                objectName: "section-" + navEntry.modelData.id
                                text: navEntry.modelData.title
                                glyph: navEntry.modelData.glyph
                                count: win.countText(navEntry.modelData.id)
                                current: win.section === navEntry.modelData.id
                                onClicked: win.go(navEntry.modelData.id)
                                Keys.onDownPressed: nextItemInFocusChain(true).forceActiveFocus()
                                Keys.onUpPressed: nextItemInFocusChain(false).forceActiveFocus()
                            }
                        }
                    }
                }
            }
            // this device: where installs go and whether the controller answered
            Rectangle {
                objectName: "deviceCard"
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Look.radiusCard - 2
                color: Look.surface
                border.width: 1; border.color: Look.hairline
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("Controller, %1").arg(win.deviceText)
                RowLayout {
                    anchors { fill: parent; leftMargin: Look.space3; rightMargin: Look.space3 }
                    spacing: Look.space3
                    Glyph { name: "server"; size: 18; stroke: 1.8; color: Look.textSecondary }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 0
                        TaosText { text: qsTr("Controller"); font.pixelSize: Look.body; font.weight: Font.DemiBold }
                        TaosText { text: win.deviceText; Layout.fillWidth: true; elide: Text.ElideRight
                                   font.pixelSize: Look.small; color: Look.textSecondary }
                    }
                    Rectangle { width: 8; height: 8; radius: 4
                                color: engine.loaded && !engine.usingSnapshot ? Look.success : Look.warning }
                }
            }
        }
    }

    ColumnLayout {
        id: content
        anchors { left: sidebar.right; right: parent.right; top: parent.top; bottom: parent.bottom }
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        spacing: 0

        // narrow: search and sections on top
        ColumnLayout {
            visible: win.narrow
            Layout.fillWidth: true; Layout.margins: Look.space3; Layout.bottomMargin: Look.space1; spacing: Look.space2
            TaosField {
                id: searchNarrow
                Layout.fillWidth: true
                leftPadding: 34
                placeholderText: qsTr("Search apps, agents, models")
                accessibleName: qsTr("Search the Store")
                text: win.query
                onTextChanged: { win.query = text; if (text !== "") win.detailId = "" }
                Glyph { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary }
            }
            Flickable {
                Layout.fillWidth: true; Layout.preferredHeight: 30
                contentWidth: pillRow.implicitWidth; clip: true; flickableDirection: Flickable.HorizontalFlick
                Row {
                    id: pillRow; spacing: 6
                    Repeater {
                        model: win.sectionDefs
                        delegate: Pill { required property var modelData; text: modelData.title; checked: win.section === modelData.id
                                         onClicked: win.go(modelData.id) }
                    }
                }
            }
        }

        // what the page is showing, in one quiet line: the saved catalogue when the controller did not answer
        Rectangle {
            id: status
            objectName: "statusLine"
            visible: engine.statusText !== "" && win.section !== "webapps" && win.section !== "themes"
            Layout.fillWidth: true
            Layout.leftMargin: Look.space5; Layout.rightMargin: Look.space5; Layout.topMargin: Look.space3
            implicitHeight: 32
            radius: 16
            color: Look.field
            border.width: 1; border.color: Look.hairline
            Accessible.role: Accessible.StaticText
            Accessible.name: engine.statusText
            RowLayout {
                anchors { fill: parent; leftMargin: Look.space3; rightMargin: Look.space1 }
                spacing: Look.space2
                Rectangle { width: 6; height: 6; radius: 3; color: Look.warning }
                TaosText { Layout.fillWidth: true; text: engine.statusText; elide: Text.ElideRight
                           font.pixelSize: Look.small; color: Look.textSecondary }
                PillButton { text: qsTr("Try again"); compact: true; variant: "outline"; Layout.preferredHeight: 24
                             accessibleName: qsTr("Try the controller again"); onClicked: engine.refresh() }
            }
        }
        // a plain line when a loaded catalogue has since failed to refresh
        TaosText {
            visible: engine.errorText !== "" && !engine.usingSnapshot && win.section !== "webapps" && win.section !== "themes"
            objectName: "errorLine"
            Layout.fillWidth: true; Layout.leftMargin: Look.space5; Layout.rightMargin: Look.space5; Layout.topMargin: Look.space3
            wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
            text: qsTr("The catalogue could not be refreshed. %1").arg(engine.errorText)
            Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text
        }

        Item {
            id: pageHost
            Layout.fillWidth: true; Layout.fillHeight: true
            // restrained page change: a short fade
            NumberAnimation { id: fade; target: pageHost; property: "opacity"; from: 0; to: 1; duration: Look.fast + 40; easing.type: Look.ease }
            property string key: win.shown + "/" + win.detailId
            onKeyChanged: fade.restart()

            DiscoverPage { anchors.fill: parent; engine: engine; visible: win.shown === "discover" && win.detailId === ""
                           onOpenDetail: (id) => win.openApp(id); onGotoSection: (s) => win.go(s) }
            ListPage { anchors.fill: parent; engine: engine; section: "search"; title: qsTr("Results for “%1”").arg(win.query)
                       query: win.query; visible: win.shown === "search" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            ListPage { anchors.fill: parent; engine: engine; section: "apps"; title: qsTr("Apps"); query: win.query
                       blurb: qsTr("Services and agent frameworks that run on your controller.")
                       visible: win.shown === "apps" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            WebAppsPage { anchors.fill: parent; engine: engine; query: win.query; visible: win.shown === "webapps" && win.detailId === ""
                          onOpenDetail: (id) => win.openApp(id); onAddWebsite: addDialog.open() }
            ListPage { anchors.fill: parent; engine: engine; section: "agents"; title: qsTr("Agents"); query: win.query
                       blurb: qsTr("Agent frameworks: the harness an agent runs in.")
                       visible: win.shown === "agents" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            ListPage { anchors.fill: parent; engine: engine; section: "models"; title: qsTr("Models"); query: win.query
                       blurb: engine.profileId !== "" ? qsTr("Marked by how well they run on this device (%1).").arg(engine.profileId) : qsTr("Marked by how well they run on this device.")
                       visible: win.shown === "models" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            ListPage { anchors.fill: parent; engine: engine; section: "mcp"; title: qsTr("MCP Servers"); query: win.query
                       blurb: qsTr("Tool servers your agents can call.")
                       visible: win.shown === "mcp" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            ThemesPage { anchors.fill: parent; engine: engine; query: win.query; visible: win.shown === "themes" && win.detailId === "" }
            ListPage { anchors.fill: parent; engine: engine; section: "plugins"; title: qsTr("Plugins"); query: win.query
                       blurb: qsTr("Tools and tool servers your agents can use.")
                       visible: win.shown === "plugins" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            InstalledPage { anchors.fill: parent; engine: engine; query: win.query; visible: win.shown === "installed" && win.detailId === ""
                            onOpenDetail: (id) => win.openApp(id); onGotoSection: (s) => win.go(s) }
            ListPage { anchors.fill: parent; engine: engine; section: "updates"; title: qsTr("Updates"); query: win.query
                       blurb: qsTr("Installed apps with a newer version.")
                       visible: win.shown === "updates" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            DetailPage { id: detail; anchors.fill: parent; engine: engine; appId: win.detailId; visible: win.detailId !== ""
                         onBack: win.detailId = ""; onGotoSection: (s) => win.go(s) }
        }
    }

    AddWebsiteDialog { id: addDialog; engine: engine }

    // a short message that says what just happened
    Rectangle {
        id: toast
        objectName: "toast"
        visible: win.toastText !== ""
        anchors { horizontalCenter: content.horizontalCenter; bottom: parent.bottom; bottomMargin: 20 }
        width: Math.min(toastLabel.implicitWidth + 32, content.width - 40)
        height: toastLabel.implicitHeight + 20
        radius: Look.radiusControl
        color: Look.raised
        border.width: 1; border.color: Look.hairline
        opacity: visible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Look.base } }
        Accessible.role: Accessible.AlertMessage
        Accessible.name: win.toastText
        TaosText { id: toastLabel; anchors.centerIn: parent; width: parent.width - 32; text: win.toastText; wrapMode: Text.Wrap
                   horizontalAlignment: Text.AlignHCenter }
    }
}
