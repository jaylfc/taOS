import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import Poc

Window {
    id: root
    width: 360
    height: 800
    visible: false
    title: "taos-poc-qt"
    color: Theme.ground

    readonly property int cardHeight: 320
    readonly property int cardBottomGap: 120

    // 1. wallpaper, scaled to cover
    Image {
        id: wall
        anchors.fill: parent
        source: PocConfig.wallpaper
        fillMode: Image.PreserveAspectCrop
        asynchronous: false
        onStatusChanged: if (status === Image.Error) console.warn("wallpaper failed to load: " + source)
    }

    // 3. list (under the control row)
    Rectangle {
        id: listBg
        x: 16
        y: 56
        width: parent.width - 32
        height: parent.height - 56
        color: Theme.surface
        opacity: 0.7
    }
    ListView {
        id: list
        x: 16
        y: 56
        width: parent.width - 32
        height: parent.height - 56
        clip: true
        model: 500
        reuseItems: true
        delegate: Item {
            required property int index
            width: ListView.view.width
            height: 56
            Rectangle {   // 24 px icon glyph
                id: icon
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                width: 24; height: 24; radius: 12
                color: "#3D8BFD"
                Rectangle { anchors.centerIn: parent; width: 10; height: 10; radius: 5; color: "#FFFFFF" }
            }
            Column {
                anchors.left: icon.right
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                Text { text: "Row " + index; color: Theme.text; font.pixelSize: 16 }
                Text { text: "Second line for row " + index; color: Theme.text; opacity: 0.7; font.pixelSize: 13 }
            }
        }
    }

    // 2. control row
    Row {
        id: controls
        x: 16
        y: 0
        width: parent.width - 32
        height: 56
        spacing: 8
        TextField {
            id: entry
            width: parent.width - 2 * (cardBtn.width + 8)
            height: 40
            anchors.verticalCenter: parent.verticalCenter
            placeholderText: "Type here"
            color: Theme.text
            placeholderTextColor: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.5)
            background: Rectangle { radius: 8; color: Theme.surface; border.color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.3) }
        }
        Button {
            id: cardBtn
            width: 64; height: 40
            anchors.verticalCenter: parent.verticalCenter
            text: "Card"
            onClicked: card.shown = !card.shown
            contentItem: Text { text: cardBtn.text; color: Theme.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 8; color: cardBtn.down ? Qt.darker(Theme.surface, 1.2) : Theme.surface }
        }
        Button {
            id: themeBtn
            width: 64; height: 40
            anchors.verticalCenter: parent.verticalCenter
            text: "Theme"
            onClicked: Theme.dark = !Theme.dark
            contentItem: Text { text: themeBtn.text; color: Theme.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 8; color: themeBtn.down ? Qt.darker(Theme.surface, 1.2) : Theme.surface }
        }
    }

    // 4. card
    Item {
        id: card
        property bool shown: false
        x: 16
        width: root.width - 32
        height: root.cardHeight
        y: shown ? root.height - root.cardBottomGap - root.cardHeight : root.height
        visible: y < root.height
        Behavior on y { SpringAnimation { spring: 5; damping: 0.45; mass: 1; epsilon: 0.5 } }

        ShaderEffectSource {
            id: wallRegion
            sourceItem: wall
            visible: false
            live: true
            sourceRect: Qt.rect(card.x, card.y, card.width, card.height)
        }
        Rectangle {
            id: mask
            anchors.fill: parent
            radius: 16
            visible: false
            layer.enabled: true
        }
        MultiEffect {
            anchors.fill: parent
            source: wallRegion
            visible: !PocConfig.noBlur
            blurEnabled: true
            blur: 1.0
            blurMax: Math.max(1, Math.min(64, PocConfig.blurRadius))
            autoPaddingEnabled: false
            maskEnabled: true
            maskSource: mask
        }
        Rectangle {
            anchors.fill: parent
            radius: 16
            color: Qt.rgba(Theme.surface.r, Theme.surface.g, Theme.surface.b, 0.55)
        }
        Text {
            anchors.centerIn: parent
            text: "Card over blurred wallpaper"
            color: Theme.text
            font.pixelSize: 16
        }
    }

    // POC_AUTOCARD: after 2 s toggle n times, 1200 ms apart
    Timer {
        id: autoCard
        property int left: PocConfig.autoCard
        interval: 1200
        repeat: true
        running: false
        onTriggered: {
            card.shown = !card.shown
            if (--left <= 0) stop()
        }
    }
    Timer {
        interval: 2000
        running: PocConfig.autoCard > 0
        onTriggered: { card.shown = !card.shown; if (--autoCard.left > 0) autoCard.start() }
    }

    // POC_AUTOSCROLL: top -> bottom -> top, n times, 3000 px/s
    SequentialAnimation {
        id: autoScroll
        loops: Math.max(1, PocConfig.autoScroll)
        NumberAnimation {
            target: list; property: "contentY"
            from: 0; to: Math.max(0, list.contentHeight - list.height)
            duration: Math.max(1, Math.round(Math.max(0, list.contentHeight - list.height) / 3000 * 1000))
        }
        NumberAnimation {
            target: list; property: "contentY"
            from: Math.max(0, list.contentHeight - list.height); to: 0
            duration: Math.max(1, Math.round(Math.max(0, list.contentHeight - list.height) / 3000 * 1000))
        }
    }
    Timer {
        interval: 2000
        running: PocConfig.autoScroll > 0
        onTriggered: autoScroll.start()
    }
}
