// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// layout.launcher "text": a calm home. The time, then a few favourite names in large type and nothing else: no icons, no
// grid. Type to find anything; swipe up for every app, swipe down for the system actions, double tap to lock. The idea
// (names in large type, search over everything, gestures) follows the behaviour of Olauncher (github.com/tanujnotes/Olauncher,
// GPL-3.0); nothing of its code, text or design is used. Good as a phone home screen: set it with `taos-launcher daemon --style text`.
Item {
    id: m
    property bool showAll: false
    signal requestText(string text)
    onShowAllChanged: Launcher.menu.setLayout(showAll ? "text-all" : "text")
    Component.onCompleted: Launcher.menu.setLayout("text")
    onVisibleChanged: if (visible) { showAll = false; Launcher.menu.setLayout("text") }

    ActionsList { visible: Launcher.actionsMode; anchors.fill: parent; plain: true }
    Item {
        visible: !Launcher.actionsMode && !m.showAll
        anchors.fill: parent
        Column {
            id: home
            x: Style.space(24)
            width: parent.width - Style.space(48)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)
            Text {
                id: clock
                text: Qt.formatTime(new Date(), "h:mm")
                color: Style.textSecondary
                font.family: Style.fontDataStack
                font.pixelSize: Style.fontSize + 18
                font.weight: Font.Light
                Timer { running: m.visible; interval: 10000; repeat: true; onTriggered: clock.text = Qt.formatTime(new Date(), "h:mm") }
            }
            Item { width: 1; height: Style.space(16) }
            Repeater {
                model: Launcher.menu.pinned
                delegate: Item {
                    id: fav
                    required property int index
                    required property string appId
                    required property string name
                    width: home.width
                    height: Style.space(48)
                    readonly property bool cur: Launcher.menu.zone === "pinned" && Launcher.menu.index === index
                    Accessible.role: Accessible.Button
                    Accessible.name: name
                    Accessible.onPressAction: Launcher.menu.activate("pinned", index)
                    Rectangle { x: -Style.space(14); anchors.verticalCenter: parent.verticalCenter; width: Style.space(3); height: Style.space(24); radius: 1.5; color: Style.accent; visible: fav.cur }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width
                        text: fav.name
                        elide: Text.ElideRight
                        color: fav.cur ? Style.text : Style.textSecondary
                        font.family: Style.fontDisplayStack
                        font.pixelSize: Style.fontSize + 14
                        font.weight: Font.Normal
                        font.capitalization: Style.upperTitles ? Font.AllUppercase : Font.MixedCase
                        Behavior on color { ColorAnimation { duration: Style.reduceMotion ? 0 : 120 } }
                    }
                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        onEntered: Launcher.menu.hover("pinned", fav.index)
                        onClicked: (e) => { if (e.button === Qt.RightButton) Launcher.menu.openContextFor("pinned", fav.index); else Launcher.menu.activate("pinned", fav.index) }
                        onPressAndHold: Launcher.menu.openContextFor("pinned", fav.index)
                        Accessible.ignored: true
                    }
                }
            }
            Text {
                visible: Launcher.menu.pinnedCount === 0
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("Pin a few apps to see their names here.")
                color: Style.textSecondary
                font.family: Style.fontStack
                font.pixelSize: Style.fontSize + 4
            }
        }
        Text {
            anchors.left: parent.left; anchors.leftMargin: Style.space(24)
            anchors.bottom: parent.bottom; anchors.bottomMargin: Style.space(24)
            width: parent.width - Style.space(48)
            wrapMode: Text.WordWrap
            text: qsTr("Type to search. Swipe up for all apps, down for actions. Double tap to lock.")
            color: Style.textTertiary
            font.family: Style.fontStack
            font.pixelSize: Style.fontSize - 1
            Accessible.ignored: true
        }
    }
    Item {
        visible: !Launcher.actionsMode && m.showAll
        anchors.fill: parent
        AllApps { anchors.fill: parent; anchors.leftMargin: Style.space(12); showHeader: false; plain: true; showGroup: false }
    }
    // gestures: a drag that ends far enough is a swipe
    DragHandler {
        id: drag
        target: null
        xAxis.enabled: false
        onActiveChanged: {
            if (active) return
            const dy = centroid.position.y - centroid.pressPosition.y
            if (dy < -Style.space(72)) m.showAll = true
            else if (dy > Style.space(72)) { if (m.showAll) m.showAll = false; else m.requestText(">") }
        }
    }
    TapHandler { acceptedButtons: Qt.LeftButton; gesturePolicy: TapHandler.ReleaseWithinBounds; onDoubleTapped: Launcher.menu.quickAction("system/lock") }
}
