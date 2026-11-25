// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// Every app, grouped by category or by letter, with a header that switches between the two.
Item {
    id: aa
    property bool showHeader: true
    property bool plain: false
    property bool showGroup: true
    readonly property real headH: showHeader ? Style.space(36) : 0
    function ensureVisible() { if (Launcher.menu.zone === "all") list.positionViewAtIndex(Launcher.menu.index, ListView.Contain) }
    Connections { target: Launcher.menu; function onCursorChanged() { aa.ensureVisible() } }
    Component.onCompleted: Launcher.menu.setColumns("all", 1)

    Item {
        id: head
        visible: aa.showHeader
        width: parent.width
        height: aa.headH
        SectionLabel { x: Style.space(16); anchors.verticalCenter: parent.verticalCenter; text: qsTr("All apps") }
        // the two ways to read the list: a small segmented switch
        Row {
            anchors.right: parent.right
            anchors.rightMargin: Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            spacing: 0
            Repeater {
                model: [ { key: "category", label: qsTr("Category") }, { key: "az", label: qsTr("A-Z") } ]
                delegate: Rectangle {
                    required property var modelData
                    readonly property bool on: Launcher.menu.sort === modelData.key
                    width: lab.implicitWidth + Style.space(16)
                    height: Style.space(24)
                    radius: Style.radius(8)
                    color: on ? Style.raised : "transparent"
                    border.width: on ? Math.max(1, Style.borderWidth) : 0
                    border.color: Style.hairline
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    Accessible.checked: on
                    Text { id: lab; anchors.centerIn: parent; text: modelData.label; color: parent.on ? Style.text : Style.textSecondary
                           font.family: Style.fontStack; font.pixelSize: Style.fontSize - 2; font.weight: Font.Medium; Accessible.ignored: true }
                    MouseArea { anchors.fill: parent; onClicked: Launcher.menu.setSort(modelData.key); Accessible.ignored: true }
                }
            }
        }
    }
    ListView {
        id: list
        y: aa.headH
        width: parent.width
        height: parent.height - aa.headH
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: Launcher.menu.all
        cacheBuffer: 2000
        Accessible.role: Accessible.List
        Accessible.name: qsTr("All apps")
        delegate: Column {
            id: dl
            required property int index
            required property string appId
            required property string name
            required property string icon
            required property bool hasIcon
            required property string group
            required property string section
            required property bool firstInSection
            width: ListView.view.width
            SectionLabel {
                visible: dl.firstInSection
                height: visible ? Style.space(32) : 0
                leftPadding: Style.space(16)
                verticalAlignment: Text.AlignBottom
                bottomPadding: Style.space(4)
                text: dl.section
            }
            AppRow {
                width: parent.width
                appId: dl.appId; name: dl.name; icon: dl.icon; hasIcon: dl.hasIcon; group: aa.showGroup ? dl.group : ""
                plain: aa.plain
                selected: Launcher.menu.zone === "all" && Launcher.menu.index === dl.index
                onActivated: Launcher.menu.activate("all", dl.index)
                onContextRequested: Launcher.menu.openContextFor("all", dl.index)
                onHovered: Launcher.menu.hover("all", dl.index)
            }
        }
        footer: Item { width: 1; height: Style.space(8) }
    }
    // a thin scroll indicator, only when the list overflows
    Rectangle {
        visible: list.contentHeight > list.height + 1
        x: parent.width - Style.space(6)
        width: Style.space(3)
        radius: width / 2
        color: Style.scrollbar
        y: aa.headH + Style.space(4) + (list.height - Style.space(8) - height) * (list.contentY / Math.max(1, list.contentHeight - list.height))
        height: Math.max(Style.space(24), (list.height - Style.space(8)) * list.height / Math.max(1, list.contentHeight))
    }
}
