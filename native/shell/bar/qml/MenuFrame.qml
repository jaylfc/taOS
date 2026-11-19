import QtQuick
import Taos.Bar

// The window of a bar menu: a clear layer surface over the whole output (the
// click that dismisses the menu lands on it) and the frosted panel under the
// icon, with the same open and close motion as the network menu. Children go
// into the panel's column. Escape closes; Up and Down move between rows.
FocusScope {
    id: root
    property int barHeight: 40
    property real anchorRight: width - 16
    property string title: ""
    property int panelWidth: 348
    property bool closing: false
    default property alias content: col.data
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"

    focus: true
    Accessible.role: Accessible.Pane
    Accessible.name: title

    function beginClose() {
        if (closing) return
        closing = true
        if (Theme.reduceMotion) MenuHost.finishClose()
        else closeAnim.start()
    }
    function moveFocus(forward) {
        var cur = root.Window.window ? root.Window.window.activeFocusItem : null
        var n = cur ? cur.nextItemInFocusChain(forward) : null
        if (n) n.forceActiveFocus()
    }
    Keys.onEscapePressed: beginClose()
    Keys.onDownPressed: moveFocus(true)
    Keys.onUpPressed: moveFocus(false)
    Component.onCompleted: {
        root.forceActiveFocus()
        if (!Theme.reduceMotion) openAnim.start(); else panel.opacity = 1
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: root.beginClose()
        Accessible.ignored: true
    }
    Item {
        id: panel
        width: root.panelWidth
        x: Math.max(8, Math.min(root.width - width - 8, root.anchorRight - width))
        y: root.barHeight + 6
        height: Math.min(flick.contentHeight + 16, root.height - y - 12)
        opacity: 0
        transformOrigin: Item.TopRight
        Accessible.role: Accessible.Pane
        Accessible.name: root.title

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }
        MenuPanel { anchors.fill: parent }
        ParallelAnimation {
            id: openAnim
            NumberAnimation { target: panel; property: "opacity"; from: 0; to: 1; duration: 150; easing.type: Easing.OutCubic }
            NumberAnimation { target: panel; property: "scale"; from: 0.96; to: 1; duration: 190; easing.type: Easing.OutCubic }
            NumberAnimation { target: panel; property: "y"; from: root.barHeight - 2; to: root.barHeight + 6; duration: 190; easing.type: Easing.OutCubic }
        }
        ParallelAnimation {
            id: closeAnim
            NumberAnimation { target: panel; property: "opacity"; to: 0; duration: 110; easing.type: Easing.InCubic }
            NumberAnimation { target: panel; property: "scale"; to: 0.97; duration: 110; easing.type: Easing.InCubic }
            onFinished: MenuHost.finishClose()
        }
        Flickable {
            id: flick
            anchors.fill: parent
            anchors.margins: 8
            contentWidth: width
            contentHeight: col.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            Column { id: col; width: flick.width; spacing: 2 }
        }
    }
}
