// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0

// The one sign-in for every native app. The desktop's shared session ($XDG_RUNTIME_DIR/taos/session)
// normally signs an app in without this; it is the fallback when that session is missing or was refused.
// Signing in here stores the session for the whole desktop, so every other open app signs in by itself.
// A controller that cannot be reached is not a sign-in problem and says so, with a way to try again.
Item {
    id: root
    property string appName: ""
    implicitWidth: 340
    implicitHeight: panel.height
    GlassPanel {
        id: panel
        anchors.centerIn: parent
        strong: true
        width: Math.min(parent.width - 48, 340)
        height: form.implicitHeight + 40
        ColumnLayout {
            id: form
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
            spacing: 10
            TaosText {
                text: Taos.session.unreachable ? qsTr("Can't reach the controller") : qsTr("Sign in to taOS")
                font.pixelSize: Look.title; font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            TaosText {
                visible: !Taos.session.unreachable
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: (root.appName !== "" ? qsTr("%1 needs your taOS session.").arg(root.appName) + " " : "")
                      + qsTr("Sign in once and every app on this desktop is signed in.")
                color: Look.textSecondary; font.pixelSize: Look.small
            }
            TaosText {
                visible: Taos.session.unreachable
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("taOS is not answering. Your sign-in is kept; this tries again when the controller is back.")
                color: Look.textSecondary; font.pixelSize: Look.small
            }
            TaosField { id: user; visible: !Taos.session.unreachable; Layout.fillWidth: true
                        placeholderText: qsTr("Username"); accessibleName: qsTr("Username")
                        KeyNavigation.tab: pass }
            TaosField { id: pass; visible: !Taos.session.unreachable; Layout.fillWidth: true
                        placeholderText: qsTr("Password"); accessibleName: qsTr("Password")
                        echoMode: TextInput.Password
                        onAccepted: if (user.text !== "" && text !== "") Taos.session.signIn(user.text, text) }
            TaosText {
                visible: Taos.session.errorText !== "" && !Taos.session.unreachable
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
                text: Taos.session.unreachable ? qsTr("Try again")
                      : (Taos.session.state === Session.SigningIn ? qsTr("Signing in...") : qsTr("Sign in"))
                variant: "primary"
                enabled: Taos.session.state !== Session.SigningIn
                         && (Taos.session.unreachable || (user.text !== "" && pass.text !== ""))
                onClicked: Taos.session.unreachable ? Taos.session.start() : Taos.session.signIn(user.text, pass.text)
            }
        }
    }
}
