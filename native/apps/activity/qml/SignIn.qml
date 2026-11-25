// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Taos 1.0
import Taos.Style 1.0

// Shown only when the shared desktop session is missing or was refused.
Item {
    id: root
    property string reason: ""
    Glass {
        anchors.centerIn: parent
        width: Math.min(360, parent.width - 32)
        height: form.implicitHeight + 48
        ColumnLayout {
            id: form
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }
            spacing: 12
            Txt { text: Taos.session.unreachable ? qsTr("Can't reach the controller") : qsTr("Sign in to taOS"); font.pixelSize: 15; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
            Txt {
                text: Taos.session.unreachable ? qsTr("taOS is not answering. Your sign-in is kept; try again when the controller is back.")
                                               : qsTr("Activity needs your taOS session. Sign in once and every app on this desktop is signed in.")
                font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; opacity: 0.8
            }
            Txt {
                visible: text.length > 0 && !Taos.session.unreachable
                text: root.reason || Taos.session.errorText
                color: Pal.crit; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true
            }
            TextField {
                id: user; visible: !Taos.session.unreachable; Layout.fillWidth: true; placeholderText: qsTr("Username"); Accessible.name: qsTr("Username")
                font.family: Pal.family; palette.text: TaosStyle.text; palette.base: TaosStyle.raised
                KeyNavigation.tab: pass
            }
            TextField {
                id: pass; visible: !Taos.session.unreachable; Layout.fillWidth: true; placeholderText: qsTr("Password"); echoMode: TextInput.Password
                Accessible.name: qsTr("Password")
                font.family: Pal.family; palette.text: TaosStyle.text; palette.base: TaosStyle.raised
                onAccepted: Taos.session.signIn(user.text, text)
            }
            GlassButton {
                text: Taos.session.unreachable ? qsTr("Try again") : (Taos.session.state === Session.SigningIn ? qsTr("Signing in…") : qsTr("Sign in"))
                prominent: true
                enabled: Taos.session.state !== Session.SigningIn
                Layout.alignment: Qt.AlignRight
                onClicked: Taos.session.unreachable ? Taos.session.start() : Taos.session.signIn(user.text, pass.text)
            }
        }
    }
}
