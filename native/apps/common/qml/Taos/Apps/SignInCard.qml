import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import "Logic.js" as Logic

// The sign-in broker card (desktop/src/components/SignInActions.tsx): open the
// agent's sign-in page in the system browser, paste the code it shows. The code
// goes to the broker (POST /api/auth-broker/requests/{rid}/code {code}), never
// to the agent. The page host is checked against the broker's allowlist again
// here, so a card the user opens never leads to a site the agent chose.
Item {
    id: root
    property string requestId: ""
    property string url: ""
    property string host: ""
    property string agent: "An agent"
    property bool busy: false
    property string errorText: ""
    signal resolved()

    implicitHeight: col.implicitHeight
    implicitWidth: col.implicitWidth
    Accessible.role: Accessible.Grouping
    Accessible.name: "Sign-in actions"

    function sendCode() {
        var trimmed = codeField.text.trim()
        if (trimmed === "") return
        busy = true
        errorText = ""
        Taos.http("POST", "/api/auth-broker/requests/" + encodeURIComponent(requestId) + "/code", {},
                  { code: trimmed }, function (ok, value, err) {
            if (!root) return
            if (!ok) { root.errorText = err || "Request failed"; root.busy = false; return }
            codeField.text = ""
            root.resolved()
        })
    }

    ColumnLayout {
        id: col
        width: parent.width
        spacing: 8
        TaosText {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Look.textSecondary
            font.pixelSize: Look.small
            textFormat: Text.StyledText
            text: "<b>" + root.agent.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</b> needs you to sign in at <b>"
                  + root.host + "</b>, then paste the code it shows."
        }
        TaosButton {
            text: "Open"
            glyph: "external"
            variant: "primary"
            compact: true
            accessibleName: "Open " + root.host + " sign-in for " + root.agent
            onClicked: Qt.openUrlExternally(root.url)
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            TaosField {
                id: codeField
                Layout.fillWidth: true
                placeholderText: "Paste code"
                accessibleName: "Sign-in code for " + root.agent
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                onAccepted: root.sendCode()
            }
            TaosButton {
                text: "Send code"
                glyph: "key"
                variant: "primary"
                accessibleName: "Send code to " + root.agent
                enabled: !root.busy && codeField.text.trim() !== ""
                onClicked: root.sendCode()
            }
        }
        TaosText {
            visible: root.errorText !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Look.danger
            font.pixelSize: Look.small
            text: root.errorText
            Accessible.ignored: false
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
        }
    }
}
