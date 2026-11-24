import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The left pane: sections (Channels, Agents-DMs, Direct Messages, Remote) with
// unread counts. Headers and rows are one flat model so a section can collapse.
Item {
    id: root
    required property var engine
    property alias list: list
    signal chosen()
    signal switcherRequested()
    objectName: "channelList"

    Rectangle { anchors.fill: parent; color: Look.surface }
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Look.hairline }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 12
            Layout.bottomMargin: 6
            spacing: 8
            TaosText {
                text: qsTr("Messages")
                font.pixelSize: Look.title
                font.weight: Font.DemiBold
                Accessible.ignored: false
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                visible: root.engine.totalUnread > 0
                implicitWidth: Math.max(20, tot.implicitWidth + 12); height: 18; radius: 9
                color: Look.raised; border.width: 1; border.color: Look.hairline
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("%1 unread").arg(root.engine.totalUnread)
                TaosText { id: tot; anchors.centerIn: parent; text: root.engine.totalUnread
                           font.pixelSize: Look.caption; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
            }
            C.AbstractButton {
                id: switchBtn
                objectName: "switcherButton"
                implicitWidth: 28; implicitHeight: 28
                focusPolicy: Qt.StrongFocus
                hoverEnabled: true
                Accessible.role: Accessible.Button
                Accessible.name: qsTr("Switch conversation, Control K")
                onClicked: root.switcherRequested()
                background: Rectangle { radius: 6; color: switchBtn.hovered ? Look.hover : "transparent"
                    border.width: switchBtn.visualFocus ? 2 : 0; border.color: Look.focusRing }
                contentItem: MIcon { anchors.centerIn: parent; name: "search"; size: 15; color: Look.textSecondary }
            }
        }

        // loading skeleton
        Column {
            Layout.fillWidth: true
            Layout.margins: 12
            spacing: 10
            visible: root.engine.channelsLoading && !root.engine.channelsLoaded
            objectName: "channelSkeleton"
            Repeater {
                model: 7
                Rectangle {
                    width: parent.width * (index % 3 === 0 ? 0.8 : 0.95); height: 34; radius: 6
                    color: Look.field
                    SequentialAnimation on opacity {
                        running: !Look.reduceMotion && parent.visible; loops: Animation.Infinite
                        NumberAnimation { to: 0.5; duration: 700 }
                        NumberAnimation { to: 1.0; duration: 700 }
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: (root.engine.channelsError !== "" && !root.engine.channelsLoaded) || (root.engine.channelsLoaded && list.count === 0)
            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - 32
                spacing: 12
                MEmpty {
                    Layout.fillWidth: true
                    visible: root.engine.channelsError !== "" && !root.engine.channelsLoaded
                    glyph: "alert"; tone: "error"
                    title: qsTr("Can't load conversations")
                    detail: root.engine.channelsError
                    objectName: "channelsError"
                }
                TaosButton {
                    Layout.alignment: Qt.AlignHCenter
                    visible: root.engine.channelsError !== "" && !root.engine.channelsLoaded
                    text: qsTr("Try again")
                    objectName: "channelsRetry"
                    onClicked: root.engine.refresh()
                }
                MEmpty {
                    Layout.fillWidth: true
                    visible: root.engine.channelsLoaded && list.count === 0
                    glyph: "inbox"
                    title: qsTr("No conversations yet")
                    detail: qsTr("Channels and agent chats show up here.")
                    objectName: "channelsEmpty"
                }
            }
        }

        ListView {
            id: list
            objectName: "channelListView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.engine.channelsLoaded || count > 0
            model: root.engine.channels
            clip: true
            currentIndex: -1
            boundsBehavior: Flickable.StopAtBounds
            keyNavigationEnabled: false
            Accessible.role: Accessible.List
            Accessible.name: qsTr("Conversations")
            C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }
            delegate: Item {
                id: row
                required property var model
                required property int index
                width: list.width
                height: model.isHeader ? 30 : 46
                C.AbstractButton {
                    id: btn
                    anchors.fill: parent
                    anchors.leftMargin: 6; anchors.rightMargin: 6
                    focusPolicy: Qt.StrongFocus
                    hoverEnabled: true
                    readonly property bool isSel: !row.model.isHeader && row.model.channelId === root.engine.selectedId
                    objectName: row.model.isHeader ? "section_" + row.model.section : "channel_" + row.model.channelId
                    Accessible.role: Accessible.Button
                    Accessible.name: row.model.isHeader
                        ? qsTr("%1 section, %2 conversations%3").arg(row.model.section).arg(row.model.count)
                              .arg(row.model.collapsed ? qsTr(", collapsed") : "")
                        : row.model.name + (row.model.unread > 0 ? qsTr(", %1 unread").arg(row.model.unread) : "")
                          + (row.model.presence !== "" ? ", " + row.model.presence : "")
                    Accessible.checked: isSel
                    onClicked: {
                        if (row.model.isHeader) root.engine.toggleSection(row.model.section)
                        else { const id = row.model.channelId; root.chosen(); root.engine.openChannel(id) }
                    }
                    Keys.onUpPressed: if (row.index > 0) list.itemAtIndex(row.index - 1)?.forceFocusInside()
                    Keys.onDownPressed: list.itemAtIndex(row.index + 1)?.forceFocusInside()
                    background: Rectangle {
                        radius: 7
                        color: btn.isSel ? Look.selection : btn.hovered || btn.visualFocus ? Look.hover : "transparent"
                        border.width: btn.visualFocus ? 2 : 0
                        border.color: Look.focusRing
                        Behavior on color { ColorAnimation { duration: Look.fast } }
                    }
                    contentItem: Item {
                        // section header
                        RowLayout {
                            visible: row.model.isHeader
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            spacing: 6
                            Glyph { name: row.model.collapsed ? "chevron-right" : "chevron-down"; size: 12; color: Look.textTertiary }
                            TaosText {
                                text: row.model.section.toUpperCase()
                                font.pixelSize: Look.caption; font.weight: Font.DemiBold; font.letterSpacing: 0.6
                                color: Look.textTertiary
                            }
                            Item { Layout.fillWidth: true }
                            TaosText {
                                visible: row.model.unread > 0
                                text: row.model.unread
                                font.pixelSize: Look.caption; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                                color: Look.text
                            }
                        }
                        // channel row
                        RowLayout {
                            visible: !row.model.isHeader
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            spacing: 8
                            Item {
                                Layout.preferredWidth: 14; Layout.preferredHeight: 14
                                MIcon {
                                    anchors.centerIn: parent
                                    visible: row.model.type !== "dm" || row.model.presence === ""
                                    name: row.model.type === "dm" || row.model.type === "dm-remote" ? "at" : "hash"
                                    size: 14; color: Look.textTertiary
                                }
                                // presence of an agent: filled = live, pulsing = working, ring = idle
                                Rectangle {
                                    id: pres
                                    anchors.centerIn: parent
                                    visible: row.model.type === "dm" && row.model.presence !== ""
                                    width: 9; height: 9; radius: 5
                                    color: row.model.presence === "idle" ? "transparent" : row.model.presence === "working" ? Look.warning : Look.success
                                    border.width: row.model.presence === "idle" ? 1.5 : 0
                                    border.color: Look.textTertiary
                                    SequentialAnimation on opacity {
                                        running: row.model.presence === "working" && !Look.reduceMotion; loops: Animation.Infinite
                                        NumberAnimation { to: 0.35; duration: 600 }
                                        NumberAnimation { to: 1.0; duration: 600 }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                TaosText {
                                    Layout.fillWidth: true
                                    text: row.model.name
                                    elide: Text.ElideRight
                                    font.weight: row.model.unread > 0 ? Font.DemiBold : Font.Normal
                                    color: row.model.muted ? Look.textSecondary : Look.text
                                }
                                TaosText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: row.model.preview
                                    elide: Text.ElideRight
                                    font.pixelSize: Look.small
                                    color: Look.textTertiary
                                }
                            }
                            Rectangle {
                                visible: row.model.unread > 0
                                implicitWidth: Math.max(20, cnt.implicitWidth + 10); implicitHeight: 18; radius: 9
                                color: Look.raised; border.width: 1; border.color: Look.hairline
                                TaosText { id: cnt; anchors.centerIn: parent; text: row.model.unread
                                           font.pixelSize: Look.caption; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
                            }
                        }
                    }
                }
                function forceFocusInside() { btn.forceActiveFocus(Qt.TabFocusReason) }
            }
        }
    }
}
