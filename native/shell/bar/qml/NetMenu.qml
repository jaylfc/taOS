// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The network menu: Wi-Fi, Ethernet and Bluetooth in one frosted panel that
// drops from the network icon. This window covers the whole output (a layer
// surface); the clear area around the panel catches the click that closes it.
FocusScope {
    id: root
    property int barHeight: 40
    property real anchorRight: width - 16
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    readonly property int panelWidth: 348
    property bool closing: false
    property string statusText: ""

    focus: true
    Accessible.role: Accessible.Pane
    Accessible.name: qsTr("Network and Bluetooth")

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
        Network.refresh()
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
        scale: 1
        Accessible.role: Accessible.Pane
        Accessible.name: qsTr("Network and Bluetooth")

        // Swallow clicks on the panel so they do not reach the closing area.
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

            Column {
                id: col
                width: flick.width
                spacing: 2

                // ---- Bluetooth request (pairing code, PIN, permission) ----
                Rectangle {
                    id: card
                    visible: Bluetooth.requestKind !== 0
                    width: parent.width
                    height: visible ? cardCol.implicitHeight + 24 : 0
                    radius: 10
                    color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.07)
                    border.width: 1
                    border.color: Theme.stroke
                    Accessible.role: Accessible.Dialog
                    Accessible.name: cardTitle.text
                    onVisibleChanged: if (visible) Qt.callLater(function () { cardDefault.forceActiveFocus() })
                    Column {
                        id: cardCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 8
                        Text {
                            id: cardTitle
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            color: Theme.text
                            font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold
                            text: {
                                switch (Bluetooth.requestKind) {
                                case 1: return qsTr("Pair with %1?").arg(Bluetooth.requestDevice)
                                case 2: case 3: return qsTr("Enter this code on %1").arg(Bluetooth.requestDevice)
                                case 4: return qsTr("Enter the PIN for %1").arg(Bluetooth.requestDevice)
                                case 5: return qsTr("Enter the passkey for %1").arg(Bluetooth.requestDevice)
                                case 6: return qsTr("Allow %1 to connect?").arg(Bluetooth.requestDevice)
                                default: return ""
                                }
                            }
                        }
                        Text {
                            visible: Bluetooth.requestKind === 1 || Bluetooth.requestKind === 2 || Bluetooth.requestKind === 3
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: Bluetooth.requestCode.length === 6 ? Bluetooth.requestCode.slice(0, 3) + " " + Bluetooth.requestCode.slice(3) : Bluetooth.requestCode
                            color: Theme.text
                            font.family: root.fontFamily; font.pixelSize: 26; font.weight: Font.DemiBold
                            font.features: { "tnum": 1 }
                            Accessible.role: Accessible.StaticText
                            Accessible.name: qsTr("Code %1").arg(Bluetooth.requestCode.split("").join(" "))
                        }
                        Text {
                            visible: Bluetooth.requestKind === 1
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            color: Theme.textSecondary
                            font.family: root.fontFamily; font.pixelSize: 11
                            text: qsTr("Make sure the same code is showing on the device.")
                        }
                        PillField {
                            id: pinField
                            visible: Bluetooth.requestKind === 4 || Bluetooth.requestKind === 5
                            width: parent.width
                            numeric: Bluetooth.requestKind === 5
                            placeholder: Bluetooth.requestKind === 5 ? qsTr("Passkey") : qsTr("PIN")
                            actionName: qsTr("Pair")
                            onSubmitted: function (t) { Bluetooth.respond(true, t); pinField.clear() }
                            onEscaped: Bluetooth.cancelPairing()
                        }
                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 8
                            Rectangle {
                                width: 96; height: 28; radius: 14
                                color: cancelTap.pressed ? Theme.pressed : cancelTap.containsMouse || activeFocus ? Theme.hover : "transparent"
                                border.width: 1; border.color: Theme.stroke
                                activeFocusOnTab: true
                                Accessible.role: Accessible.Button
                                Accessible.name: Bluetooth.requestKind === 6 ? qsTr("Don't allow") : qsTr("Cancel")
                                Accessible.onPressAction: Bluetooth.cancelPairing()
                                Keys.onReturnPressed: Bluetooth.cancelPairing()
                                Keys.onSpacePressed: Bluetooth.cancelPairing()
                                Text { anchors.centerIn: parent; text: parent.Accessible.name; color: Theme.text; font.family: root.fontFamily; font.pixelSize: 12; font.weight: Font.Medium }
                                MouseArea { id: cancelTap; anchors.fill: parent; hoverEnabled: true; onClicked: Bluetooth.cancelPairing() }
                            }
                            Rectangle {
                                id: cardDefault
                                visible: Bluetooth.requestKind === 1 || Bluetooth.requestKind === 6
                                width: 96; height: 28; radius: 14
                                color: Theme.text
                                opacity: okTap.pressed ? 0.7 : 1
                                activeFocusOnTab: true
                                Accessible.role: Accessible.Button
                                Accessible.name: Bluetooth.requestKind === 6 ? qsTr("Allow") : qsTr("Pair")
                                Accessible.onPressAction: Bluetooth.respond(true)
                                Keys.onReturnPressed: Bluetooth.respond(true)
                                Keys.onSpacePressed: Bluetooth.respond(true)
                                Rectangle { anchors.fill: parent; anchors.margins: -2.5; radius: height / 2; color: "transparent"; border.width: 1.5; border.color: Theme.text; opacity: parent.activeFocus ? 0.45 : 0 }
                                Text { anchors.centerIn: parent; text: parent.Accessible.name; color: Theme.ground; font.family: root.fontFamily; font.pixelSize: 12; font.weight: Font.DemiBold }
                                MouseArea { id: okTap; anchors.fill: parent; onClicked: Bluetooth.respond(true) }
                            }
                        }
                    }
                }

                // ---- Wi-Fi ----
                Item {
                    width: parent.width
                    height: 32
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Wi-Fi")
                        color: Theme.text
                        font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }
                    MenuSwitch {
                        visible: Wifi.switchVisible
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        label: qsTr("Wi-Fi")
                        checked: Wifi.wifiEnabled
                        onToggled: function (on) { Wifi.setWifiEnabled(on) }
                    }
                }
                Text {
                    visible: Wifi.wifiMessage.length > 0
                    width: parent.width
                    leftPadding: 10; rightPadding: 10; bottomPadding: 6
                    wrapMode: Text.WordWrap
                    text: Wifi.wifiMessage
                    color: Theme.textSecondary
                    font.family: root.fontFamily; font.pixelSize: 12
                    Accessible.role: Accessible.StaticText
                    Accessible.name: text
                }
                Repeater {
                    model: Wifi.wifiMessage.length === 0 ? Wifi.networks : null
                    delegate: Column {
                        id: wd
                        required property string ssid
                        required property int strength
                        required property bool secured
                        required property bool active
                        required property bool known
                        required property bool connecting
                        required property bool supported
                        required property string description
                        width: col.width
                        MenuRow {
                            title: wd.ssid
                            accessibleName: wd.description
                            accessibleHint: wd.known || wd.active ? qsTr("Press Delete to %1").arg(wd.active ? qsTr("disconnect") : qsTr("forget")) : ""
                            emphasized: wd.active
                            actionText: wd.active ? qsTr("Disconnect") : wd.known ? qsTr("Forget") : ""
                            onActivated: Wifi.connectTo(wd.ssid)
                            onActionTriggered: wd.active ? Wifi.disconnectWifi() : Wifi.forget(wd.ssid)
                            SignalGlyph { strength: wd.strength; anchors.verticalCenter: parent.verticalCenter; anchors.horizontalCenter: parent.horizontalCenter }
                            trailing: [
                                Row {
                                    spacing: 6
                                    anchors.verticalCenter: parent.verticalCenter
                                    Spinner { visible: wd.connecting; running: wd.connecting; anchors.verticalCenter: parent.verticalCenter }
                                    LockGlyph { visible: wd.secured && !wd.active; anchors.verticalCenter: parent.verticalCenter }
                                    CheckGlyph { visible: wd.active && !wd.connecting; anchors.verticalCenter: parent.verticalCenter }
                                }
                            ]
                        }
                        Item {
                            id: pwBox
                            visible: Wifi.passwordSsid === wd.ssid
                            width: parent.width
                            height: visible ? 42 : 0
                            onVisibleChanged: if (visible) { pw.clear(); Qt.callLater(function () { pw.focusInput() }); if (Wifi.passwordRetry) pw.fail() }
                            PillField {
                                id: pw
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.leftMargin: 8; anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                secret: true
                                placeholder: qsTr("Password for %1").arg(wd.ssid)
                                onSubmitted: function (t) { var s = wd.ssid; pw.clear(); Wifi.submitPassword(s, t) }
                                onEscaped: { Wifi.cancelPassword(); root.forceActiveFocus() }
                            }
                        }
                    }
                }
                MenuRow {
                    id: hiddenRow
                    property bool expanded: false
                    visible: Wifi.wifiMessage.length === 0
                    height: visible ? 34 : 0
                    title: qsTr("Other network…")
                    accessibleName: qsTr("Join a hidden network")
                    onActivated: { expanded = !expanded; if (expanded) Qt.callLater(function () { hidName.focusInput() }) }
                    Text {
                        anchors.centerIn: parent
                        text: "+"
                        color: Theme.textSecondary
                        font.family: root.fontFamily; font.pixelSize: 17; font.weight: Font.Light
                    }
                }
                Column {
                    visible: hiddenRow.expanded && Wifi.wifiMessage.length === 0
                    width: parent.width
                    spacing: 6
                    topPadding: 2; bottomPadding: 6
                    PillField {
                        id: hidName
                        x: 8; width: parent.width - 16
                        placeholder: qsTr("Network name")
                        actionName: qsTr("Next")
                        onSubmitted: hidPass.focusInput()
                        onEscaped: { hiddenRow.expanded = false; root.forceActiveFocus() }
                    }
                    PillField {
                        id: hidPass
                        x: 8; width: parent.width - 16
                        secret: true
                        allowEmpty: true
                        actionName: qsTr("Join")
                        placeholder: qsTr("Password, if it has one")
                        onSubmitted: function (t) { if (hidName.text.length === 0) { hidName.focusInput(); return } Wifi.connectHidden(hidName.text, t); hidName.clear(); hidPass.clear(); hiddenRow.expanded = false }
                        onEscaped: { hiddenRow.expanded = false; root.forceActiveFocus() }
                    }
                }

                Rectangle { width: parent.width; height: 1; color: Theme.stroke; opacity: 0.8 }

                // ---- Ethernet ----
                MenuRow {
                    visible: Network.ethernetPresent
                    height: visible ? 44 : 0
                    interactive: false
                    title: qsTr("Ethernet")
                    subtitle: Network.ethernetText
                    accessibleName: qsTr("Ethernet, %1").arg(Network.ethernetText)
                    NetworkIcon { kind: "wired"; anchors.centerIn: parent; scale: 0.9 }
                }
                Rectangle { visible: Network.ethernetPresent; width: parent.width; height: 1; color: Theme.stroke; opacity: 0.8 }

                // ---- Bluetooth ----
                Item {
                    width: parent.width
                    height: 32
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Bluetooth")
                        color: Theme.text
                        font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }
                    MenuSwitch {
                        visible: Bluetooth.switchVisible
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        label: qsTr("Bluetooth")
                        checked: Bluetooth.powered
                        onToggled: function (on) { Bluetooth.setPowered(on) }
                    }
                }
                Text {
                    visible: Bluetooth.message.length > 0
                    width: parent.width
                    leftPadding: 10; rightPadding: 10; bottomPadding: 6
                    wrapMode: Text.WordWrap
                    text: Bluetooth.message
                    color: Theme.textSecondary
                    font.family: root.fontFamily; font.pixelSize: 12
                    Accessible.role: Accessible.StaticText
                    Accessible.name: text
                }
                Repeater {
                    model: Bluetooth.message.length === 0 ? Bluetooth.paired : null
                    delegate: MenuRow {
                        id: pd
                        required property string path
                        required property string name
                        required property bool connected
                        required property bool busy
                        required property int battery
                        required property string description
                        title: pd.name
                        subtitle: (pd.busy ? qsTr("Working…") : pd.connected ? qsTr("Connected") : qsTr("Not connected"))
                                  + (pd.battery >= 0 ? "  ·  " + pd.battery + "%" : "")
                        accessibleName: pd.description
                        accessibleHint: qsTr("Press Enter to %1, Delete to forget").arg(pd.connected ? qsTr("disconnect") : qsTr("connect"))
                        actionText: qsTr("Forget")
                        onActivated: if (!pd.busy) { pd.connected ? Bluetooth.disconnectDevice(pd.path) : Bluetooth.connectDevice(pd.path) }
                        onActionTriggered: Bluetooth.forget(pd.path)
                        BluetoothGlyph { anchors.centerIn: parent; color: pd.connected ? Theme.accent : Theme.textSecondary }
                        trailing: [ Spinner { visible: pd.busy; running: pd.busy } ]
                    }
                }
                Text {
                    visible: Bluetooth.message.length === 0 && Bluetooth.pairedCount === 0
                    width: parent.width
                    leftPadding: 10; bottomPadding: 4
                    text: qsTr("No paired devices")
                    color: Theme.textTertiary
                    font.family: root.fontFamily; font.pixelSize: 12
                    Accessible.role: Accessible.StaticText
                    Accessible.name: text
                }
                Item {
                    visible: Bluetooth.message.length === 0 && (Bluetooth.discovering || Bluetooth.nearbyCount > 0)
                    width: parent.width
                    height: visible ? 28 : 0
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Nearby")
                        color: Theme.textSecondary
                        font.family: root.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                        Accessible.role: Accessible.Heading
                        Accessible.name: Bluetooth.discovering ? qsTr("Nearby, looking for devices") : qsTr("Nearby")
                    }
                    Spinner { visible: Bluetooth.discovering; running: Bluetooth.discovering; anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter }
                }
                Repeater {
                    model: Bluetooth.message.length === 0 ? Bluetooth.nearby : null
                    delegate: MenuRow {
                        id: nd
                        required property string path
                        required property string name
                        required property bool busy
                        required property string description
                        title: nd.name
                        subtitle: nd.busy ? qsTr("Pairing…") : qsTr("Not paired")
                        accessibleName: nd.description
                        accessibleHint: qsTr("Press Enter to pair")
                        actionText: qsTr("Pair")
                        actionAlways: true
                        onActivated: if (!nd.busy) Bluetooth.pair(nd.path)
                        onActionTriggered: if (!nd.busy) Bluetooth.pair(nd.path)
                        BluetoothGlyph { anchors.centerIn: parent; color: Theme.textTertiary }
                        trailing: [ Spinner { visible: nd.busy; running: nd.busy } ]
                    }
                }

                // ---- Errors, in plain words ----
                Rectangle {
                    id: errBox
                    readonly property string text: Bluetooth.errorText.length > 0 ? Bluetooth.errorText : Wifi.errorText
                    visible: text.length > 0
                    width: parent.width
                    height: visible ? errLabel.implicitHeight + 16 : 0
                    radius: 8
                    color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.14)
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: text
                    Text {
                        id: errLabel
                        anchors.fill: parent
                        anchors.margins: 8
                        wrapMode: Text.WordWrap
                        text: errBox.text
                        color: Theme.text
                        font.family: root.fontFamily; font.pixelSize: 12
                    }
                    MouseArea { anchors.fill: parent; onClicked: { Wifi.clearError(); Bluetooth.clearError() } }
                }
                Item { width: 1; height: 2 }
            }
        }
    }

    // Spoken, not shown: state changes go to the screen reader.
    Item { id: live; width: 1; height: 1; Accessible.role: Accessible.StaticText; Accessible.name: root.statusText }
    Connections { target: Wifi; function onAnnouncement(t) { root.statusText = t; MenuHost.announce(live, t) } }
    Connections { target: Bluetooth; function onAnnouncement(t) { root.statusText = t; MenuHost.announce(live, t) } }
}
