// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The right pane: header, transcript, typing and stall lines, composer.
Item {
    id: root
    required property var engine
    property alias composer: composer
    property alias view: view
    property bool narrow: false
    signal backRequested()
    objectName: "conversation"

    property int newCount: 0
    readonly property bool atBottom: view.contentHeight - view.contentY - view.height < 60

    Rectangle { anchors.fill: parent; color: Look.ground }

    // ---- empty: nothing chosen ----------------------------------------------------------------
    MEmpty {
        anchors.centerIn: parent
        width: Math.min(360, parent.width - 32)
        visible: root.engine.selectedId === ""
        glyph: "inbox"
        title: qsTr("Pick a conversation")
        detail: qsTr("Choose a channel or an agent on the left. Control K switches quickly.")
        objectName: "noSelection"
    }

    ColumnLayout {
        anchors.fill: parent
        visible: root.engine.selectedId !== ""
        spacing: 0

        // ---- header ---------------------------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 48
            color: Look.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Look.hairline }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                spacing: 10
                C.AbstractButton {
                    id: backBtn
                    visible: root.narrow
                    implicitWidth: 28; implicitHeight: 28
                    focusPolicy: Qt.StrongFocus; hoverEnabled: true
                    Accessible.role: Accessible.Button; Accessible.name: qsTr("Back to conversations")
                    onClicked: root.backRequested()
                    background: Rectangle { radius: 6; color: backBtn.hovered ? Look.hover : "transparent"
                        border.width: backBtn.visualFocus ? 2 : 0; border.color: Look.focusRing }
                    contentItem: Glyph { anchors.centerIn: parent; name: "chevron-right"; size: 14; rotation: 180; color: Look.textSecondary }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    TaosText {
                        Layout.fillWidth: true
                        text: root.engine.selectedName
                        font.pixelSize: Look.title; font.weight: Font.DemiBold; elide: Text.ElideRight
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                        objectName: "conversationTitle"
                    }
                    TaosText {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: root.engine.selectedTopic
                        font.pixelSize: Look.small; color: Look.textTertiary; elide: Text.ElideRight
                    }
                }
                Chip {
                    visible: !root.engine.live && root.engine.channelsLoaded
                    text: qsTr("paused"); tone: "warning"
                    accessibleName: qsTr("live updates paused")
                    objectName: "pausedChip"
                }
            }
        }

        // ---- transcript -----------------------------------------------------------------------
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: view
                objectName: "transcript"
                anchors.fill: parent
                model: root.engine.messages
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: false
                cacheBuffer: 600
                Accessible.role: Accessible.List
                Accessible.name: qsTr("Messages in %1").arg(root.engine.selectedName)
                C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }
                header: Item {
                    width: view.width; height: root.engine.hasOlder || root.engine.loadingOlder ? 36 : 12
                    TaosText { anchors.centerIn: parent; visible: root.engine.loadingOlder; text: qsTr("Loading earlier messages..."); color: Look.textTertiary; font.pixelSize: Look.small }
                }
                footer: Item { width: view.width; height: 8 }
                delegate: MessageRow { engine: root.engine }
                property real heightBefore: 0
                onContentYChanged: if (contentY < 40 && !root.engine.loadingOlder && root.engine.hasOlder && moving) { heightBefore = contentHeight; root.engine.loadOlder() }
                onAtYEndChanged: if (atYEnd) root.newCount = 0

                Connections {
                    target: root.engine
                    function onConversationLoaded() { root.newCount = 0; snap.restart() }
                    function onMessageAppended(own) {
                        if (own || root.atBottom) snap.restart()
                        else root.newCount += 1
                    }
                    function onOlderLoaded(count) { Qt.callLater(() => { view.contentY += view.contentHeight - view.heightBefore }) }
                }
                Timer { id: snap; interval: 30; onTriggered: { view.positionViewAtEnd(); view.positionViewAtEnd() } }
            }

            // loading skeleton
            Column {
                visible: root.engine.messagesLoading && view.count === 0
                objectName: "messagesSkeleton"
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                spacing: 14
                Repeater {
                    model: 5
                    Row {
                        spacing: 12
                        Rectangle { width: 28; height: 28; radius: 7; color: Look.field }
                        Column {
                            spacing: 6
                            Rectangle { width: 110; height: 10; radius: 4; color: Look.field }
                            Rectangle { width: 120 + (index * 97) % 320; height: 10; radius: 4; color: Look.field }
                        }
                        SequentialAnimation on opacity {
                            running: !Look.reduceMotion && parent.visible; loops: Animation.Infinite
                            NumberAnimation { to: 0.5; duration: 700 }
                            NumberAnimation { to: 1.0; duration: 700 }
                        }
                    }
                }
            }

            MEmpty {
                anchors.centerIn: parent
                width: Math.min(360, parent.width - 32)
                visible: root.engine.messagesError !== ""
                glyph: "alert"; tone: "error"
                title: qsTr("Can't load this conversation")
                detail: root.engine.messagesError
                objectName: "messagesError"
            }
            TaosButton {
                visible: root.engine.messagesError !== ""
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height / 2 + 60
                text: qsTr("Try again"); objectName: "messagesRetry"
                onClicked: root.engine.reloadMessages()
            }
            MEmpty {
                anchors.centerIn: parent
                width: Math.min(360, parent.width - 32)
                visible: !root.engine.messagesLoading && root.engine.messagesError === "" && view.count === 0
                glyph: "inbox"
                title: qsTr("No messages yet")
                detail: root.engine.selectedType === "dm" ? qsTr("Say hello to start the conversation.") : qsTr("Be the first to write here.")
                objectName: "noMessages"
            }

            // new messages while reading earlier ones
            C.AbstractButton {
                id: jump
                visible: !root.atBottom && view.count > 0
                objectName: "jumpToLatest"
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 10 }
                implicitHeight: 28; implicitWidth: jt.implicitWidth + 28
                focusPolicy: Qt.StrongFocus; hoverEnabled: true
                Accessible.role: Accessible.Button
                Accessible.name: root.newCount > 0 ? qsTr("Jump to latest, %1 new messages").arg(root.newCount) : qsTr("Jump to latest")
                onClicked: { view.positionViewAtEnd(); root.newCount = 0 }
                background: Rectangle { radius: 14; color: Look.raised; border.width: jump.visualFocus ? 2 : 1
                    border.color: jump.visualFocus ? Look.focusRing : Look.hairline }
                contentItem: TaosText { id: jt; anchors.centerIn: parent; font.weight: Font.Medium; font.pixelSize: Look.small
                    text: root.newCount > 0 ? qsTr("%n new", "", root.newCount) : qsTr("Latest") }
            }
        }

        // ---- typing, stall and send error -----------------------------------------------------
        Item {
            Layout.fillWidth: true
            implicitHeight: footerCol.implicitHeight
            visible: footerCol.implicitHeight > 0
            Column {
                id: footerCol
                x: 16; width: parent.width - 32
                spacing: 2
                TaosText {
                    id: typingLine
                    visible: root.engine.typingText !== ""
                    width: parent.width; text: root.engine.typingText; font.pixelSize: Look.small; color: Look.textTertiary
                    font.italic: true; wrapMode: Text.Wrap
                    objectName: "typingLine"
                    Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
                }
                TaosText {
                    visible: root.engine.stallText !== ""
                    width: parent.width; text: root.engine.stallText; font.pixelSize: Look.small
                    color: root.engine.stalled ? Look.warning : Look.textSecondary; wrapMode: Text.Wrap
                    objectName: "stallLine"
                    Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
                }
                TaosText {
                    visible: root.engine.sendError !== ""
                    width: parent.width; text: root.engine.sendError; font.pixelSize: Look.small; color: Look.danger; wrapMode: Text.Wrap
                    objectName: "sendError"
                    Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text
                }
            }
        }

        Composer {
            id: composer
            Layout.fillWidth: true
            engine: root.engine
            visible: true
        }
    }

    // announce new messages from others to assistive technology
    Item {
        id: live
        property string announcement: ""
        objectName: "liveRegion"
        width: 1; height: 1; opacity: 0
        Accessible.role: Accessible.AlertMessage
        Accessible.name: announcement
        Connections {
            target: root.engine
            function onIncomingMessage(author, text) { live.announcement = author + ": " + text }
        }
    }
}
