import QtQuick
import QtQuick.Controls
import Taos 1.0
import Taos.Style 1.0

TaosWindow {
    width: 480; height: 640
    title: qsTr("Notifications")
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.")
                  : q.running && !q.live ? qsTr("Reconnecting to live updates") : ""

    LiveQuery {
        id: q
        op: "notifications.list"
        eventKinds: ["notification.added"]
        roles: ["id", "title", "message", "read"]
        running: Taos.session.state === Session.SignedIn
    }
    Column {   // sign-in form, shown only when the Session asks for it
        anchors.centerIn: parent; spacing: TaosStyle.spacing
        visible: Taos.session.state !== Session.SignedIn
        TextField { id: user; placeholderText: qsTr("Username"); text: "alex" }
        TextField { id: pass; placeholderText: qsTr("Password"); echoMode: TextInput.Password
                    onAccepted: Taos.session.signIn(user.text, text) }
        Button { text: qsTr("Sign in"); onClicked: Taos.session.signIn(user.text, pass.text) }
        Text { text: Taos.session.errorText; color: TaosStyle.danger; font.pixelSize: TaosStyle.bodySize }
    }
    ListView {
        anchors.fill: parent; anchors.topMargin: 40; clip: true
        visible: Taos.session.state === Session.SignedIn
        model: q.model
        delegate: Rectangle {
            width: ListView.view.width; height: 56; color: index % 2 ? TaosStyle.surface : TaosStyle.raised
            Column { anchors.fill: parent; anchors.margins: 8
                Text { text: model.title; color: model.read ? TaosStyle.textSecondary : TaosStyle.text
                       font.pixelSize: TaosStyle.bodySize; font.bold: !model.read }
                Text { text: model.message; color: TaosStyle.textTertiary; font.pixelSize: TaosStyle.bodySize - 2 } }
        }
    }
}
