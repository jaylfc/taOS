// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Greeter

// The one pill-shaped field. Text, an inline round arrow that appears when
// there is something to send, a spinner while greetd thinks, a Caps Lock mark.
FocusScope {
    id: pf
    property Item blurred: null
    readonly property alias text: input.text
    readonly property real btn: height - 10 * Plan.s
    property real shakeX: 0
    property bool sent: false   // the text on screen was already sent to greetd
    property bool errored: false
    signal submitted(string text)

    function clear() { input.text = ""; sent = false; }
    // A new question arrived: the previous answer is history.
    function nextPrompt() { if (sent) clear(); }
    function focusInput() { input.forceActiveFocus(); }

    GlassPanel {
        id: glass
        anchors.fill: parent
        blurred: pf.blurred
        transform: Translate { x: pf.shakeX }
    }
    // The error state keeps a visible border without relying on colour: the
    // message line and an icon say the same thing.
    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: "transparent"
        // Focus is a quiet hairline, not a coloured ring: the field is the
        // only thing on screen that takes typing.
        border.width: Math.max(1, 1 * Plan.s)
        border.color: Style.text
        opacity: input.activeFocus ? 0.38 : 0
        transform: Translate { x: pf.shakeX }
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 150 } }
    }
    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: "transparent"
        border.width: Math.max(1.5, 1.5 * Plan.s)
        border.color: Style.danger
        opacity: pf.errored ? 1 : 0
        transform: Translate { x: pf.shakeX }
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 200 } }
    }

    Item {
        id: content
        anchors.fill: parent
        transform: Translate { x: pf.shakeX }

        TextInput {
            id: input
            focus: true
            // Symmetric margins and centred text: the caret, the placeholder and
            // the dots all share the pill's centre line, whatever sits on the right.
            readonly property real side: Math.max(pf.btn, right.width) + 10 * Plan.s
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: side
            anchors.rightMargin: side
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: TextInput.AlignHCenter
            clip: true
            color: Style.text
            selectionColor: Style.accent
            selectedTextColor: Style.accentText
            font.family: "Inter"
            font.weight: Font.Normal
            font.pixelSize: 14 * Plan.s
            echoMode: Greeter.promptSecret ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            inputMethodHints: Greeter.promptSecret ? (Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase) : Qt.ImhNoAutoUppercase
            readOnly: Greeter.phase === 3
            // No caret over the placeholder: with centred text it would cut through
            // the words. It appears with the first character and blinks as usual.
            cursorDelegate: Rectangle {
                id: caret
                width: Math.max(1.5, 1.5 * Plan.s); color: Style.text; radius: 1
                visible: input.activeFocus && input.text.length > 0
                SequentialAnimation on opacity {
                    running: caret.visible && !Style.reduceMotion; loops: Animation.Infinite
                    NumberAnimation { to: 1; duration: 0 } PauseAnimation { duration: 530 }
                    NumberAnimation { to: 0; duration: 0 } PauseAnimation { duration: 530 }
                }
            }
            Accessible.role: Accessible.EditableText
            Accessible.name: Greeter.placeholder
            Accessible.description: Greeter.promptSecret ? qsTr("Secret entry. Press Enter to sign in") : qsTr("Press Enter to continue")
            Accessible.passwordEdit: Greeter.promptSecret
            onAccepted: pf.trySubmit()
            Text {
                anchors.fill: parent
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Text.AlignHCenter
                visible: input.text.length === 0 && !input.inputMethodComposing
                text: Greeter.placeholder
                color: Style.textSecondary
                font: input.font
                elide: Text.ElideRight
            }
        }
        Row {
            id: right
            anchors.right: parent.right
            anchors.rightMargin: 5 * Plan.s
            anchors.verticalCenter: parent.verticalCenter
            spacing: 5 * Plan.s
            // Caps Lock mark: an up arrow over a bar, inside a small outline.
            Item {
                id: caps
                visible: Caps.capsLock
                width: pf.btn * 0.62; height: width
                anchors.verticalCenter: parent.verticalCenter
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("Caps Lock is on")
                Rectangle {
                    anchors.fill: parent
                    radius: width * 0.28
                    color: "transparent"
                    border.width: 1.5
                    border.color: Style.textSecondary
                }
                Shape {
                    id: capsGlyph
                    anchors.centerIn: parent
                    width: parent.width * 0.5; height: width
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: Style.text; strokeWidth: 1.6; fillColor: "transparent"
                        capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                        startX: capsGlyph.width / 2; startY: 0
                        PathLine { x: capsGlyph.width * 0.9; y: capsGlyph.width * 0.5 }
                        PathLine { x: capsGlyph.width * 0.65; y: capsGlyph.width * 0.5 }
                        PathLine { x: capsGlyph.width * 0.65; y: capsGlyph.width * 0.85 }
                        PathLine { x: capsGlyph.width * 0.35; y: capsGlyph.width * 0.85 }
                        PathLine { x: capsGlyph.width * 0.35; y: capsGlyph.width * 0.5 }
                        PathLine { x: capsGlyph.width * 0.1; y: capsGlyph.width * 0.5 }
                        PathLine { x: capsGlyph.width / 2; y: 0 }
                    }
                }
            }
            Item {
                id: action
                width: pf.btn; height: pf.btn
                anchors.verticalCenter: parent.verticalCenter
                readonly property bool busy: Greeter.busy
                readonly property bool shown: busy || input.text.length > 0
                opacity: shown ? 1 : 0
                scale: shown ? 1 : 0.6
                Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 140 } }
                Behavior on scale { enabled: !Style.reduceMotion; NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
                activeFocusOnTab: false
                Accessible.role: Accessible.Button
                Accessible.name: busy ? qsTr("Signing in") : qsTr("Sign in")
                Accessible.onPressAction: pf.trySubmit()
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    color: Style.text
                    opacity: arrowTap.pressed ? 0.7 : (arrowHover.hovered ? 1.0 : 0.92)
                }
                // Arrow (right) while idle.
                Shape {
                    id: arrow
                    anchors.centerIn: parent
                    width: parent.width * 0.44; height: width
                    visible: !action.busy
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: Style.ground; strokeWidth: Math.max(1.6, 1.6 * Plan.s)
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                        startX: 0; startY: arrow.height / 2
                        PathLine { x: arrow.width; y: arrow.height / 2 }
                        PathMove { x: arrow.width * 0.55; y: arrow.height * 0.1 }
                        PathLine { x: arrow.width; y: arrow.height / 2 }
                        PathLine { x: arrow.width * 0.55; y: arrow.height * 0.9 }
                    }
                }
                // Spinner while busy.
                Shape {
                    id: spin
                    anchors.centerIn: parent
                    width: parent.width * 0.5; height: width
                    visible: action.busy
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: Style.ground; strokeWidth: Math.max(2, 2 * Plan.s)
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap
                        PathAngleArc {
                            centerX: spin.width / 2; centerY: spin.height / 2
                            radiusX: spin.width / 2 - 1; radiusY: spin.height / 2 - 1
                            startAngle: -90; sweepAngle: 270
                        }
                    }
                    RotationAnimator on rotation {
                        running: action.busy && action.visible
                        from: 0; to: 360; duration: Style.reduceMotion ? 2400 : 900
                        loops: Animation.Infinite
                    }
                }
                HoverHandler { id: arrowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { id: arrowTap; enabled: action.shown && !action.busy; onTapped: pf.trySubmit() }
            }
        }
    }

    function trySubmit() {
        if (Greeter.phase === 3 || Greeter.phase === 0 || Greeter.phase > 3) return;
        if (input.text.length === 0 && Greeter.promptSecret) return;
        var t = input.text;
        pf.sent = true;
        pf.submitted(t);
    }

    SequentialAnimation {
        id: shake
        PropertyAnimation { target: pf; property: "shakeX"; to: -9 * Plan.s; duration: 55; easing.type: Easing.OutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: 8 * Plan.s; duration: 80; easing.type: Easing.InOutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: -5 * Plan.s; duration: 70; easing.type: Easing.InOutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: 3 * Plan.s; duration: 60; easing.type: Easing.InOutQuad }
        PropertyAnimation { target: pf; property: "shakeX"; to: 0; duration: 60; easing.type: Easing.OutQuad }
    }
    function playFailure() {
        input.text = "";
        errored = true;
        if (!Style.reduceMotion) shake.restart();
    }
    Connections {
        target: input
        function onTextChanged() { if (input.text.length > 0) pf.errored = false; }
    }
}
