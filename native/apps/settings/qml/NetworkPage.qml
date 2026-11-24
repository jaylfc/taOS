import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "network"
    readonly property var wifi: Settings.wifi
    readonly property var bt: Settings.bluetooth
    // Scanning and pairing run only while this page is on screen.
    readonly property bool shown: visible && Window.window && Window.window.visible
    onShownChanged: Settings.setNetworkActive(shown)
    Component.onCompleted: Settings.setNetworkActive(shown)
    Component.onDestruction: Settings.setNetworkActive(false)

    Group {
        heading: qsTr("Connection")
        SRow {
            settingId: "network.status"
            description: !page.wifi.available ? qsTr("NetworkManager is not running, so only what is known is shown.")
                       : page.wifi.kind === "wifi" ? qsTr("Connected to %1").arg(page.wifi.activeSsid)
                       : page.wifi.kind === "wired" ? qsTr("Connected by cable") : qsTr("Not connected")
            Txt { text: page.wifi.ethernetPresent ? page.wifi.ethernetText : ""; color: TaosStyle.textSecondary; wrapMode: Text.NoWrap }
        }
    }

    Txt {
        width: parent.width
        visible: page.wifi.errorText.length > 0
        text: page.wifi.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }
    Group {
        heading: qsTr("Wi-Fi")
        visible: page.wifi.switchVisible
        SRow {
            settingId: "network.wifi"
            note: page.wifi.wifiMessage
            Toggle { label: qsTr("Wi-Fi"); checked: page.wifi.wifiEnabled; onFlipped: (on) => page.wifi.setWifiEnabled(on) }
        }
        Repeater {
            model: page.wifi.wifiEnabled ? page.wifi.networks : null
            delegate: Item {
                id: net
                required property string ssid
                required property int strength
                required property bool secured
                required property bool active
                required property bool known
                required property bool connecting
                required property bool supported
                required property string description
                width: parent ? parent.width : 400
                implicitHeight: netRow.implicitHeight + (ask.visible ? ask.implicitHeight + 12 : 0)
                readonly property bool asking: page.wifi.passwordSsid === ssid
                Accessible.role: Accessible.ListItem
                Accessible.name: ssid
                Accessible.description: description
                Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 } height: 1; color: TaosStyle.hairline }
                RowLayout {
                    id: netRow
                    x: 16; width: parent.width - 32; height: 48
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Txt { text: net.ssid; Layout.fillWidth: true; elide: Text.ElideRight; wrapMode: Text.NoWrap; font.weight: net.active ? Font.DemiBold : Font.Normal; Accessible.ignored: true }
                        Txt { text: net.description; Layout.fillWidth: true; font.pixelSize: 12; color: TaosStyle.textSecondary; elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                    }
                    Btn { visible: net.active; text: qsTr("Disconnect"); onClicked: page.wifi.disconnectWifi() }
                    Btn { visible: net.known && !net.active; text: qsTr("Forget"); onClicked: page.wifi.forget(net.ssid) }
                    Btn {
                        visible: !net.active
                        enabled: net.supported && !net.connecting
                        text: net.connecting ? qsTr("Joining...") : qsTr("Join")
                        onClicked: page.wifi.connectTo(net.ssid)
                    }
                }
                RowLayout {
                    id: ask
                    visible: net.asking
                    x: 16; y: netRow.height + 4; width: parent.width - 32
                    spacing: 8
                    C.TextField {
                        id: pw
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        placeholderText: page.wifi.passwordRetry ? qsTr("Wrong password. Try again") : qsTr("Password")
                        color: TaosStyle.text
                        placeholderTextColor: TaosStyle.textTertiary
                        selectionColor: TaosStyle.selection
                        font.family: "Inter"
                        Accessible.name: qsTr("Password for %1").arg(net.ssid)
                        background: Rectangle { radius: 7; color: TaosStyle.track; border.width: pw.activeFocus ? 2 : 1; border.color: pw.activeFocus ? TaosStyle.focusRing : TaosStyle.hairline }
                        onAccepted: page.wifi.submitPassword(net.ssid, text)
                        onVisibleChanged: if (visible) { text = ""; forceActiveFocus() }
                    }
                    Btn { text: qsTr("Cancel"); onClicked: page.wifi.cancelPassword() }
                    Btn { text: qsTr("Join"); kind: "accent"; onClicked: page.wifi.submitPassword(net.ssid, pw.text) }
                }
            }
        }
    }
    Txt {
        width: parent.width
        visible: !page.wifi.switchVisible
        text: page.wifi.available ? qsTr("This device has no Wi-Fi.") : qsTr("Wi-Fi settings need NetworkManager, which is not running.")
        color: TaosStyle.textSecondary
    }

    Txt {
        width: parent.width
        visible: page.bt.errorText.length > 0
        text: page.bt.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }
    Group {
        heading: qsTr("Bluetooth")
        visible: page.bt.switchVisible
        SRow {
            settingId: "network.bluetooth"
            note: page.bt.message
            Toggle { label: qsTr("Bluetooth"); checked: page.bt.powered; onFlipped: (on) => page.bt.setPowered(on) }
        }
        // A pairing question from the device: confirm a code, or type one.
        Rectangle {
            id: request
            width: parent.width
            visible: page.bt.requestKind !== 0
            height: visible ? reqCol.implicitHeight + 24 : 0
            color: TaosStyle.raised
            Accessible.role: Accessible.AlertMessage
            Accessible.name: qsTr("Pairing with %1. Code %2").arg(page.bt.requestDevice).arg(page.bt.requestCode)
            ColumnLayout {
                id: reqCol
                x: 16; y: 12; width: parent.width - 32
                spacing: 8
                Txt { Layout.fillWidth: true; text: qsTr("Pairing with %1").arg(page.bt.requestDevice); font.weight: Font.DemiBold }
                Txt {
                    Layout.fillWidth: true
                    visible: page.bt.requestCode.length > 0
                    text: page.bt.requestKind === 4 || page.bt.requestKind === 5 ? qsTr("Type the code the device shows.") : qsTr("Code: %1").arg(page.bt.requestCode)
                    color: TaosStyle.textSecondary
                }
                RowLayout {
                    spacing: 8
                    C.TextField {
                        id: code
                        visible: page.bt.requestKind === 4 || page.bt.requestKind === 5
                        Layout.preferredWidth: 140
                        color: TaosStyle.text
                        font.family: "Inter"
                        Accessible.name: qsTr("Pairing code")
                        background: Rectangle { radius: 7; color: TaosStyle.track; border.width: 1; border.color: TaosStyle.hairline }
                    }
                    Btn { text: qsTr("Cancel"); onClicked: page.bt.respond(false, "") }
                    Btn { text: qsTr("Pair"); kind: "accent"; onClicked: page.bt.respond(true, code.text) }
                }
            }
        }
        Repeater {
            model: page.bt.powered ? page.bt.paired : null
            delegate: SRow {
                required property string path
                required property string name
                required property bool connected
                required property bool busy
                required property string description
                title: name
                note: description
                Btn { text: qsTr("Forget"); onClicked: page.bt.forget(path) }
                Btn {
                    enabled: !busy
                    text: connected ? qsTr("Disconnect") : qsTr("Connect")
                    onClicked: connected ? page.bt.disconnectDevice(path) : page.bt.connectDevice(path)
                }
            }
        }
    }
    Group {
        heading: qsTr("Nearby devices")
        visible: page.bt.switchVisible && page.bt.powered && page.bt.nearbyCount > 0
        Repeater {
            model: page.bt.powered ? page.bt.nearby : null
            delegate: SRow {
                required property string path
                required property string name
                required property bool busy
                required property string description
                title: name
                note: description
                Btn { enabled: !busy; text: busy ? qsTr("Pairing...") : qsTr("Pair"); onClicked: page.bt.pair(path) }
            }
        }
    }
    Txt {
        width: parent.width
        visible: !page.bt.switchVisible
        text: page.bt.available ? qsTr("This device has no Bluetooth.") : qsTr("Bluetooth settings need BlueZ, which is not running.")
        color: TaosStyle.textSecondary
    }
}
