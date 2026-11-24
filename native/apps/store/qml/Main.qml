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
    width: 1120
    height: 740
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

    readonly property var sectionDefs: engine.sections
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
        width: visible ? 216 : 0
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: 1; color: Look.hairline }
        ColumnLayout {
            anchors { fill: parent; leftMargin: 12; rightMargin: 12; topMargin: 14 }
            spacing: 10
            TaosField {
                id: search
                objectName: "searchField"
                Layout.fillWidth: true
                placeholderText: qsTr("Search the Store")
                accessibleName: qsTr("Search the Store")
                text: win.query
                onTextChanged: { win.query = text; if (text !== "") win.detailId = "" }
                Keys.onEscapePressed: { text = ""; sections.forceActiveFocus() }
                Keys.onDownPressed: sections.forceActiveFocus()
            }
            ListView {
                id: sections
                objectName: "sections"
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: win.sectionDefs
                clip: true
                keyNavigationEnabled: true
                activeFocusOnTab: true
                spacing: 2
                currentIndex: { for (let i = 0; i < win.sectionDefs.length; i++) if (win.sectionDefs[i].id === win.section) return i; return 0 }
                onCurrentIndexChanged: {}
                Keys.onReturnPressed: win.go(win.sectionDefs[currentIndex].id)
                delegate: C.ItemDelegate {
                    id: srow
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 32
                    padding: 0
                    topPadding: 0; bottomPadding: 0
                    hoverEnabled: true
                    objectName: "section-" + modelData.id
                    readonly property bool current: win.section === modelData.id
                    // a gap before the Library group
                    topInset: modelData.group === "library" && (index === 0 || win.sectionDefs[index - 1].group !== "library") ? 0 : 0
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.title + (win.countText(modelData.id) !== "" ? ", " + win.countText(modelData.id) : "")
                    Accessible.checked: current
                    onClicked: { win.go(modelData.id); sections.currentIndex = index }
                    background: Rectangle {
                        radius: 7
                        color: srow.current ? Look.selection : srow.hovered || srow.visualFocus ? Look.hover : "transparent"
                        border.width: srow.visualFocus ? 2 : 0
                        border.color: Look.focusRing
                        Behavior on color { ColorAnimation { duration: Look.fast } }
                    }
                    contentItem: RowLayout {
                        spacing: 8
                        TaosText { text: srow.modelData.title; Layout.fillWidth: true; Layout.leftMargin: 10
                                   font.weight: srow.current ? Font.DemiBold : Font.Normal }
                        TaosText { text: win.countText(srow.modelData.id); color: Look.textTertiary; font.pixelSize: Look.small
                                   font.features: { "tnum": 1 }; Layout.rightMargin: 10 }
                    }
                }
                section.property: "group"
                section.criteria: ViewSection.FullString
                section.delegate: Item {
                    required property string section
                    width: ListView.view ? ListView.view.width : 0
                    height: section === "library" ? 26 : 0
                    visible: section === "library"
                    TaosText { anchors { left: parent.left; leftMargin: 10; bottom: parent.bottom; bottomMargin: 2 }
                               text: qsTr("Library")
                               font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.textTertiary
                               font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
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
            Layout.fillWidth: true; Layout.margins: 12; Layout.bottomMargin: 4; spacing: 8
            TaosField {
                id: searchNarrow
                Layout.fillWidth: true
                placeholderText: qsTr("Search the Store")
                accessibleName: qsTr("Search the Store")
                text: win.query
                onTextChanged: { win.query = text; if (text !== "") win.detailId = "" }
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

        // the controller's trouble, in words (web apps and themes keep working)
        Rectangle {
            id: banner
            objectName: "errorBanner"
            visible: engine.errorText !== "" && win.section !== "webapps" && win.section !== "themes"
            Layout.fillWidth: true
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.topMargin: 12
            implicitHeight: bannerRow.implicitHeight + 16
            radius: Look.radiusControl
            color: Look.tint(Look.warning, 0.12)
            border.width: 1; border.color: Look.tint(Look.warning, 0.32)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: engine.errorText
            RowLayout {
                id: bannerRow
                anchors { fill: parent; margins: 8; leftMargin: 12 }
                spacing: 10
                TaosText {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small
                    text: (engine.errorKind === "unauthenticated" ? qsTr("Sign in to taOS to see the full catalogue. ")
                           : engine.errorKind === "forbidden" ? qsTr("Your account cannot see the catalogue. ") : engine.errorText + " ")
                          + qsTr("Web apps and themes work without it.")
                }
                TaosButton { text: qsTr("Try again"); compact: true; onClicked: engine.refresh() }
            }
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true

            DiscoverPage { anchors.fill: parent; engine: engine; visible: win.shown === "discover" && win.detailId === ""
                           onOpenDetail: (id) => win.openApp(id); onGotoSection: (s) => win.go(s) }
            ListPage { anchors.fill: parent; engine: engine; section: "search"; title: qsTr("Results for “%1”").arg(win.query)
                       query: win.query; visible: win.shown === "search" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            ListPage { anchors.fill: parent; engine: engine; section: "apps"; title: qsTr("Apps"); query: win.query
                       blurb: qsTr("Services and agent frameworks that run on your controller.")
                       visible: win.shown === "apps" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
            WebAppsPage { anchors.fill: parent; engine: engine; query: win.query; visible: win.shown === "webapps" && win.detailId === ""
                          onOpenDetail: (id) => win.openApp(id); onAddWebsite: addDialog.open() }
            ListPage { anchors.fill: parent; engine: engine; section: "models"; title: qsTr("Models"); query: win.query
                       blurb: engine.profileId !== "" ? qsTr("Marked by how well they run on this device (%1).").arg(engine.profileId) : qsTr("Marked by how well they run on this device.")
                       visible: win.shown === "models" && win.detailId === ""; onOpenDetail: (id) => win.openApp(id) }
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
