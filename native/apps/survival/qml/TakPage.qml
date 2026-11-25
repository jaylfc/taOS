// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// taOStak: the awareness view on the same map. People on the mesh and where they were last seen, the radios (who each
// belongs to, their settings, the firmware library), and the conversations. Everything the mesh engine heard is shown
// with who is behind it; nothing is sent unless the engine allows it and the owner has turned sending on.
StagePage {
    id: page
    takMode: true
    pageTitle: qsTr("taOStak")
    property string tab: "people"
    property string view: ""             // "" (the tab) | person | thread | settings | firmware
    property string viewArg: ""
    property var demo: ({})
    readonly property var radios: Survival.radios
    readonly property var tak: Survival.tak
    readonly property bool wideView: view === "settings" || view === "firmware"
    panelWidth: wideView ? 440 : 340
    tall: view !== ""
    function focusSearch() { stage.focusSearch() }
    function open(v, arg) { viewArg = arg; view = v; sheetOpen = true; stage.selectedPerson = v === "person" ? arg : "" }
    function back() { view = ""; viewArg = ""; stage.selectedPerson = "" }
    readonly property string viewTitle: {
        if (view === "person") { tak.people; return tak.displayName(viewArg) }
        if (view === "thread") { tak.people; const t = tak.thread(viewArg); return t.title || viewArg }
        if (view === "settings") return qsTr("Radio settings")
        if (view === "firmware") return qsTr("Firmware")
        return qsTr("taOStak")
    }
    Component.onCompleted: { map.startPosition(); radios.scan() }
    Connections { target: page.stage; function onPersonClicked(node) { page.tab = "people"; page.open("person", node) } }
    Timer { interval: 4000; repeat: true; running: page.visible && page.tab === "radios" && page.view === ""; onTriggered: page.radios.scan() }
    onTabChanged: if (tab === "radios") radios.scan()
    sidePanel: Component {
        ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Look.space4
                Layout.bottomMargin: Look.space3
                spacing: Look.space2
                TaosButton { visible: page.view !== ""; objectName: "takBack"; glyph: "arrow-left"; compact: true; variant: "ghost"; accessibleName: qsTr("Back"); onClicked: page.back() }
                TaosText { text: page.viewTitle; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                Rectangle {
                    visible: page.view === "" && page.tak.unreadTotal > 0
                    implicitWidth: Math.max(20, tb.implicitWidth + 12); implicitHeight: 20; radius: 10; color: Look.accent
                    Accessible.role: Accessible.StaticText; Accessible.name: qsTr("%1 unread").arg(page.tak.unreadTotal)
                    TaosText { id: tb; anchors.centerIn: parent; text: page.tak.unreadTotal; font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.accentText }
                }
            }
            TakBanner {
                Layout.fillWidth: true
                Layout.leftMargin: Look.space4; Layout.rightMargin: Look.space4; Layout.bottomMargin: Look.space3
                visible: page.view === "" || page.view === "thread"
            }
            Segmented {
                visible: page.view === ""
                Layout.fillWidth: true
                Layout.leftMargin: Look.space4; Layout.rightMargin: Look.space4; Layout.bottomMargin: Look.space3
                accessibleName: qsTr("taOStak panel")
                model: [{id: "people", text: qsTr("People")}, {id: "radios", text: qsTr("Radios")}, {id: "messages", text: qsTr("Messages")}]
                currentId: page.tab
                onActivated: (id) => page.tab = id
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
            Loader {
                id: body
                Layout.fillWidth: true; Layout.fillHeight: true
                sourceComponent: page.view === "person" ? personC : page.view === "thread" ? threadC : page.view === "settings" ? settingsC
                               : page.view === "firmware" ? firmwareC : page.tab === "people" ? peopleC : page.tab === "radios" ? radiosC : messagesC
                onLoaded: item.nav = page
            }
            Component { id: peopleC; TakPeople {} }
            Component { id: radiosC; TakRadios {} }
            Component { id: messagesC; TakMessages {} }
            Component { id: personC; TakPerson { node: page.viewArg } }
            Component { id: threadC; TakThread { threadId: page.viewArg } }
            Component { id: settingsC; TakSettings { radioId: page.viewArg } }
            Component { id: firmwareC; TakFirmware {} }
        }
    }
}
