import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Data 1.0
import Taos.Style 1.0
import Taos.Apps 1.0

// Notifications: the inbox of server notifications. A thin face over the
// NotificationFeed engine (the same store the bar's bell reads): everything it
// shows is the controller's state, fetched through the foundation client and
// kept current by the shared event stream.
TaosWindow {
    id: win
    width: 480
    height: 720
    minimumWidth: 360
    minimumHeight: 460
    title: qsTr("Notifications")
    objectName: "notificationsWindow"

    readonly property bool signedIn: Taos.session.state === Session.SignedIn
    property alias feed: feed
    property string toastText: ""
    property bool toastIsError: false
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    NotificationFeed {
        id: feed
        objectName: "feed"
        running: win.signedIn
        onActionFailed: (text) => win.showToast(text, true)
        onOpenAppRequested: (appId, props) => win.showToast(qsTr("Opening %1").arg(appId.replace("my.taos.", "")), false)
    }

    function showToast(text, isError) {
        toastText = text
        toastIsError = isError
        toastTimer.restart()
    }
    Timer { id: toastTimer; interval: 4000; onTriggered: win.toastText = "" }

    Backdrop { anchors.fill: parent }

    SignInForm { anchors.fill: parent; visible: !win.signedIn; appName: qsTr("Notifications") }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        visible: win.signedIn
        spacing: 0

        // ---- header ----------------------------------------------------------------
        GlassPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: header.implicitHeight + 24
            strong: true
            radius: 0
            edgeTop: false
            border.width: 0
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Look.hairline }
            ColumnLayout {
                id: header
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 16 }
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Glyph { name: "bell"; size: 18; color: Look.accent }
                    TaosText {
                        text: qsTr("Notifications")
                        font.pixelSize: Look.heading - 2
                        font.weight: Font.DemiBold
                        Accessible.ignored: false
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }
                    Rectangle {
                        visible: feed.unreadCount > 0
                        implicitWidth: Math.max(20, countText.implicitWidth + 12)
                        height: 20; radius: 10
                        color: Look.accent
                        Accessible.role: Accessible.StaticText
                        Accessible.name: qsTr("%1 unread").arg(feed.unreadCount)
                        TaosText { id: countText; anchors.centerIn: parent; text: feed.unreadCount
                                   font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.onAccent }
                    }
                    Item { Layout.fillWidth: true }
                    Chip {
                        objectName: "pausedChip"
                        visible: feed.running && feed.loaded && !feed.live
                        text: qsTr("paused")
                        tone: "warning"
                        accessibleName: qsTr("live updates paused")
                    }
                }
                // filters
                Flow {
                    id: filters
                    Layout.fillWidth: true
                    spacing: 6
                    visible: feed.totalCount > 0 || feed.levelFilter !== "" || feed.sourceFilter !== "" || feed.unreadOnly
                    component Pill: C.AbstractButton {
                        id: pill
                        property string tone: "neutral"
                        implicitHeight: 24
                        implicitWidth: pillLabel.implicitWidth + 20
                        hoverEnabled: true
                        focusPolicy: Qt.StrongFocus
                        Accessible.role: Accessible.CheckBox
                        Accessible.name: text
                        Accessible.checked: checked
                        background: Rectangle {
                            radius: 12
                            color: pill.checked ? Look.tint(pill.tone === "neutral" ? Look.accent : Look.tone(pill.tone), 0.22)
                                 : pill.hovered ? Look.hover : Look.field
                            border.width: pill.visualFocus ? 2 : 1
                            border.color: pill.visualFocus ? Look.accent
                                        : pill.checked ? Look.tint(pill.tone === "neutral" ? Look.accent : Look.tone(pill.tone), 0.5) : Look.hairline
                            Behavior on color { ColorAnimation { duration: Look.fast } }
                        }
                        contentItem: TaosText {
                            id: pillLabel
                            text: pill.text
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: Look.small
                            font.weight: Font.Medium
                            color: pill.checked ? Look.text : Look.textSecondary
                        }
                    }
                    Pill { objectName: "filterAll"; text: qsTr("All"); checked: feed.levelFilter === ""
                           onClicked: feed.levelFilter = "" }
                    Pill { objectName: "filterInfo"; text: qsTr("Info"); tone: "info"; checked: feed.levelFilter === "info"
                           onClicked: feed.levelFilter = feed.levelFilter === "info" ? "" : "info" }
                    Pill { objectName: "filterSuccess"; text: qsTr("Success"); tone: "success"; checked: feed.levelFilter === "success"
                           onClicked: feed.levelFilter = feed.levelFilter === "success" ? "" : "success" }
                    Pill { objectName: "filterWarning"; text: qsTr("Warning"); tone: "warning"; checked: feed.levelFilter === "warning"
                           onClicked: feed.levelFilter = feed.levelFilter === "warning" ? "" : "warning" }
                    Pill { objectName: "filterError"; text: qsTr("Error"); tone: "error"; checked: feed.levelFilter === "error"
                           onClicked: feed.levelFilter = feed.levelFilter === "error" ? "" : "error" }
                    Pill { objectName: "filterUnread"; text: qsTr("Unread"); checked: feed.unreadOnly
                           onClicked: feed.unreadOnly = !feed.unreadOnly }
                }
                TaosCombo {
                    id: sourceCombo
                    objectName: "sourceFilter"
                    visible: filters.visible && feed.sources.length > 1
                    Layout.fillWidth: true
                    implicitHeight: 28
                    accessibleName: qsTr("Filter by source")
                    model: [qsTr("All sources")].concat(feed.sources)
                    currentIndex: Math.max(0, model.indexOf(feed.sourceFilter))
                    onActivated: (i) => feed.sourceFilter = i === 0 ? "" : model[i]
                }
            }
        }

        // ---- list ------------------------------------------------------------------
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // A plain column of rows (at most the pinned requests plus 100): rows change height
            // as consent blocks load, and a virtualised list does not lay that out reliably.
            Flickable {
                id: list
                objectName: "notificationList"
                anchors.fill: parent
                readonly property int count: rowRepeater.count
                visible: count > 0
                clip: true
                contentWidth: width
                contentHeight: stack.height + 20
                boundsBehavior: Flickable.StopAtBounds
                Accessible.role: Accessible.List
                Accessible.name: qsTr("Notifications")
                C.ScrollBar.vertical: C.ScrollBar {
                    active: true
                    policy: C.ScrollBar.AsNeeded
                    contentItem: Rectangle {
                        implicitWidth: 6; radius: 3; color: Look.tint(Look.text, 0.25)
                        opacity: parent.active ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Look.base } }
                    }
                }
                function ensureVisible(item) {
                    const top = item.y + stack.y, bottom = top + item.height
                    if (top < contentY) contentY = Math.max(0, top - 8)
                    else if (bottom > contentY + height) contentY = Math.min(contentHeight - height, bottom - height + 8)
                }
                Column {
                    id: stack
                    y: 10
                    width: list.width
                    move: Transition { NumberAnimation { properties: "y"; duration: Look.base; easing.type: Easing.OutCubic } }
                    Repeater {
                        id: rowRepeater
                        model: feed.model
                        delegate: NotificationRow {
                            store: feed
                            onFocusNeighbour: (delta) => {
                                const it = rowRepeater.itemAt(index + delta)
                                if (!it) return
                                list.ensureVisible(it)
                                it.focusHeader()
                            }
                        }
                    }
                }
            }

            TaosText {
                visible: list.count === 0 && !feed.loaded && feed.errorText === ""
                anchors.centerIn: parent
                text: qsTr("Loading...")
                color: Look.textTertiary
            }
            EmptyState {
                objectName: "emptyState"
                anchors.centerIn: parent
                visible: list.count === 0 && feed.loaded && feed.totalCount === 0 && feed.errorText === ""
                glyph: "bell"
                title: qsTr("No notifications")
                detail: qsTr("Agent requests, updates and alerts show up here.")
            }
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 12
                visible: list.count === 0 && feed.loaded && feed.totalCount > 0
                EmptyState { glyph: "filter"; title: qsTr("Nothing matches"); detail: qsTr("No notification fits these filters.")
                             Layout.alignment: Qt.AlignHCenter }
                TaosButton { Layout.alignment: Qt.AlignHCenter; text: qsTr("Clear filters")
                             onClicked: { feed.levelFilter = ""; feed.sourceFilter = ""; feed.unreadOnly = false } }
            }
            ColumnLayout {
                objectName: "errorState"
                anchors.centerIn: parent
                spacing: 12
                visible: list.count === 0 && feed.errorText !== ""
                EmptyState { glyph: "alert"; tone: "error"; title: qsTr("Can't load notifications"); detail: feed.errorText
                             Layout.alignment: Qt.AlignHCenter }
                TaosButton { Layout.alignment: Qt.AlignHCenter; text: qsTr("Try again"); glyph: "history"; onClicked: feed.refresh() }
            }
            // A failed refresh with rows on screen: keep the rows, say so.
            Rectangle {
                visible: list.count > 0 && feed.errorText !== ""
                anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 8 }
                radius: 12; height: 24; width: staleText.implicitWidth + 24
                color: Look.tint(Look.warning, 0.2); border.width: 1; border.color: Look.tint(Look.warning, 0.4)
                TaosText { id: staleText; anchors.centerIn: parent; text: qsTr("Could not refresh. Showing the last list.")
                           font.pixelSize: Look.caption; color: Look.warning }
            }
        }

        // ---- footer ----------------------------------------------------------------
        GlassPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            strong: true
            radius: 0
            border.width: 0
            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Look.hairline }
            RowLayout {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                spacing: 8
                TaosButton {
                    objectName: "markAllRead"
                    visible: feed.totalCount > 0
                    glyph: "checks"; text: qsTr("Mark all read")
                    enabled: feed.unreadCount > 0
                    onClicked: feed.markAllRead()
                }
                TaosButton {
                    objectName: "clearAll"
                    visible: feed.totalCount > 0
                    glyph: "trash"; text: qsTr("Clear all")
                    onClicked: feed.clearAll()
                }
                Item { Layout.fillWidth: true }
                TaosButton {
                    objectName: "viewHistory"
                    glyph: "history"; text: qsTr("View history"); variant: "primary"
                    onClicked: feed.requestOpen("my.taos.notification-history")
                }
            }
        }
    }

    // transient message (action failures, open-app requests)
    Rectangle {
        id: toast
        visible: opacity > 0
        opacity: win.toastText !== "" ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Look.base } }
        anchors { bottom: parent.bottom; bottomMargin: 66; horizontalCenter: parent.horizontalCenter }
        width: Math.min(parent.width - 32, toastLabel.implicitWidth + 28)
        height: 34; radius: 17
        color: Look.dark ? "#26262B" : "#FFFFFF"
        border.width: 1
        border.color: win.toastIsError ? Look.tint(Look.danger, 0.5) : Look.glassStroke
        Accessible.role: win.toastIsError ? Accessible.AlertMessage : Accessible.StaticText
        Accessible.name: win.toastText
        TaosText {
            id: toastLabel
            anchors.centerIn: parent
            width: Math.min(implicitWidth, toast.width - 24)
            text: win.toastText
            elide: Text.ElideRight
            color: win.toastIsError ? Look.danger : Look.text
        }
    }
}
