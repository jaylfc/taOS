// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Bar

// The pill-shaped text field of the login screen, at menu size: centred text,
// a round arrow that appears once there is something to send, an error ring
// with a short shake. Used for Wi-Fi passwords, network names and PINs.
FocusScope {
    id: pf
    property string placeholder: ""
    property bool secret: false
    property bool numeric: false
    property bool errored: false
    property bool busy: false
    property bool allowEmpty: false
    property string actionName: qsTr("Join")
    property alias text: input.text
    property real shakeX: 0
    readonly property real btn: height - 8
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    signal submitted(string text)
    signal escaped()

    implicitHeight: 34
    height: implicitHeight
    function focusInput() { input.forceActiveFocus() }
    function clear() { input.text = "" }
    function fail() { errored = true; if (!Theme.reduceMotion) shake.restart() }

    Rectangle {
        id: pill
        anchors.fill: parent
        radius: height / 2
        transform: Translate { x: pf.shakeX }
        color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, Theme.variant === "light" ? 0.06 : 0.09)
        border.width: 1
        border.color: pf.errored ? Theme.danger : Theme.stroke
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: 1
            border.color: Theme.text
            opacity: input.activeFocus && !pf.errored ? 0.38 : 0
            Behavior on opacity { enabled: !Theme.reduceMotion; NumberAnimation { duration: 150 } }
        }
    }
    Item {
        anchors.fill: parent
        transform: Translate { x: pf.shakeX }
        TextInput {
            id: input
            focus: true
            readonly property real side: pf.btn + 12
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: side
            anchors.rightMargin: side
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: TextInput.AlignHCenter
            clip: true
            color: Theme.text
            selectionColor: Theme.accent
            selectedTextColor: Theme.accentText
            font.family: pf.fontFamily
            font.pixelSize: 13
            echoMode: pf.secret ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            inputMethodHints: (pf.secret ? Qt.ImhSensitiveData | Qt.ImhNoPredictiveText : Qt.ImhNone)
                              | (pf.numeric ? Qt.ImhDigitsOnly : Qt.ImhNoAutoUppercase)
            cursorDelegate: Rectangle {
                id: caret
                width: 1.5; color: Theme.text; radius: 1
                visible: input.activeFocus && input.text.length > 0
                SequentialAnimation on opacity {
                    running: caret.visible && !Theme.reduceMotion; loops: Animation.Infinite
                    NumberAnimation { to: 1; duration: 0 } PauseAnimation { duration: 530 }
                    NumberAnimation { to: 0; duration: 0 } PauseAnimation { duration: 530 }
                }
            }
            Accessible.role: Accessible.EditableText
            Accessible.name: pf.placeholder
            Accessible.passwordEdit: pf.secret
            Accessible.description: qsTr("Press Enter to continue, Escape to cancel")
            onAccepted: pf.send()
            onTextChanged: if (text.length > 0) pf.errored = false
            Keys.onEscapePressed: pf.escaped()
            Text {
                anchors.fill: parent
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Text.AlignHCenter
                visible: input.text.length === 0 && !input.inputMethodComposing
                text: pf.placeholder
                color: Theme.textSecondary
                font: input.font
                elide: Text.ElideRight
            }
        }
        Item {
            id: go
            width: pf.btn; height: pf.btn
            anchors.right: parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            readonly property bool shown: pf.busy || pf.allowEmpty || input.text.length > 0
            opacity: shown ? 1 : 0
            scale: shown ? 1 : 0.6
            Behavior on opacity { enabled: !Theme.reduceMotion; NumberAnimation { duration: 140 } }
            Behavior on scale { enabled: !Theme.reduceMotion; NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
            Accessible.role: Accessible.Button
            Accessible.name: pf.actionName
            Accessible.ignored: !shown
            Accessible.onPressAction: pf.send()
            Rectangle {
                anchors.fill: parent
                radius: width / 2
                color: Theme.text
                opacity: tap.pressed ? 0.7 : 0.92
            }
            Shape {
                id: arrow
                anchors.centerIn: parent
                width: parent.width * 0.46; height: width
                visible: !pf.busy
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Theme.ground; strokeWidth: 1.6; fillColor: "transparent"
                    capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    startX: 0; startY: arrow.height / 2
                    PathLine { x: arrow.width; y: arrow.height / 2 }
                    PathMove { x: arrow.width * 0.55; y: arrow.height * 0.1 }
                    PathLine { x: arrow.width; y: arrow.height / 2 }
                    PathLine { x: arrow.width * 0.55; y: arrow.height * 0.9 }
                }
            }
            Spinner { anchors.centerIn: parent; width: parent.width * 0.5; height: width; running: pf.busy; color: Theme.ground }
            MouseArea { id: tap; anchors.fill: parent; enabled: go.shown && !pf.busy; onClicked: pf.send() }
        }
    }
    function send() {
        if (input.text.length === 0 && !pf.allowEmpty) return
        pf.submitted(input.text)
    }
    SequentialAnimation {
        id: shake
        PropertyAnimation { target: pf; property: "shakeX"; to: -8; duration: 55; easing.type: Easing.OutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: 7; duration: 80; easing.type: Easing.InOutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: -4; duration: 70; easing.type: Easing.InOutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: 0; duration: 60; easing.type: Easing.OutQuad }
    }
}
