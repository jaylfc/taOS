// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Messages 1.0
import Taos.Style 1.0
import Taos.Apps 1.0

// Messages: the taOS chat. A thin face over ChatEngine, which holds every list, the
// socket and the send path. Open straight into a conversation with
// `taos-messages --channel <id>` or `--agent <name>` (a second launch switches the
// running window).
TaosWindow {
    id: win
    width: 1000
    height: 680
    minimumWidth: 360
    minimumHeight: 460
    title: qsTr("Messages")
    objectName: "messagesWindow"

    readonly property bool signedIn: Taos.session.state === Session.SignedIn
    readonly property bool narrow: width < 700
    property bool showList: true
    property alias engine: chat
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    ChatEngine {
        id: chat
        objectName: "engine"
        running: win.signedIn
        windowActive: win.active
        onSelectedChanged: if (win.narrow) win.showList = chat.selectedId === ""
    }

    Component.onCompleted: chat.applyArguments(Taos.arguments)
    Connections {
        target: Taos
        function onActivated(args) { chat.applyArguments(args) }
    }

    Shortcut { sequences: ["Ctrl+K"]; onActivated: switcher.show() }
    Shortcut { sequences: ["Alt+Up"]; onActivated: win.step(-1) }
    Shortcut { sequences: ["Alt+Down"]; onActivated: win.step(1) }
    Shortcut { sequences: ["Ctrl+L"]; onActivated: conv.composer.focusInput() }
    function step(d) {
        const hits = chat.searchChannels("")
        if (hits.length === 0) return
        let i = 0
        for (; i < hits.length; ++i) if (hits[i].channelId === chat.selectedId) break
        chat.openChannel(hits[Math.max(0, Math.min(hits.length - 1, i + d))].channelId)
    }

    SignInForm { anchors.fill: parent; visible: !win.signedIn; appName: qsTr("Messages") }

    RowLayout {
        anchors.fill: parent
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        visible: win.signedIn
        spacing: 0

        ChannelList {
            id: list
            engine: chat
            Layout.fillHeight: true
            Layout.preferredWidth: win.narrow ? parent.width : 280
            visible: !win.narrow || win.showList
            onChosen: { win.showList = false; conv.composer.focusInput() }
            onSwitcherRequested: switcher.show()
        }
        Conversation {
            id: conv
            engine: chat
            narrow: win.narrow
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !win.narrow || !win.showList
            onBackRequested: win.showList = true
        }
    }

    Switcher {
        id: switcher
        engine: chat
        onChosen: (id) => { chat.openChannel(id); win.showList = false; conv.composer.focusInput() }
    }
}
