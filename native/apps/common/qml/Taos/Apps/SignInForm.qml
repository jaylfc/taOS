// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0

// Shown while the Session has no credentials. The desktop's shared session
// normally signs the app in without this; it is the fallback.
Item {
    id: root
    property string appName: ""
    GlassPanel {
        anchors.centerIn: parent
        strong: true
        width: Math.min(parent.width - 48, 340)
        height: form.implicitHeight + 40
        ColumnLayout {
            id: form
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
            spacing: 10
            TaosText { text: "Sign in to taOS"; font.pixelSize: Look.title; font.weight: Font.DemiBold }
            TaosText { visible: root.appName !== ""; text: root.appName + " needs your taOS session."
                       color: Look.textSecondary; font.pixelSize: Look.small }
            TaosField { id: user; Layout.fillWidth: true; placeholderText: "Username"; accessibleName: "Username"
                        KeyNavigation.tab: pass }
            TaosField { id: pass; Layout.fillWidth: true; placeholderText: "Password"; accessibleName: "Password"
                        echoMode: TextInput.Password
                        onAccepted: Taos.session.signIn(user.text, text) }
            TaosText {
                visible: Taos.session.errorText !== ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: Taos.session.errorText
                color: Look.danger
                font.pixelSize: Look.small
                Accessible.ignored: false
                Accessible.role: Accessible.AlertMessage
                Accessible.name: text
            }
            TaosButton {
                Layout.fillWidth: true
                text: Taos.session.state === Session.SigningIn ? "Signing in..." : "Sign in"
                variant: "primary"
                enabled: Taos.session.state !== Session.SigningIn && user.text !== "" && pass.text !== ""
                onClicked: Taos.session.signIn(user.text, pass.text)
            }
        }
    }
}
