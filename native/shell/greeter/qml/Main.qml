import QtQuick
import QtQuick.Window
import QtQuick.Effects
import Taos.Greeter

Window {
    id: win
    visible: true
    title: "taOS login"
    color: Style.ground
    visibility: initialSize ? Window.Windowed : Window.FullScreen
    width: initialSize ? initialSize.width : 1920
    height: initialSize ? initialSize.height : 1080

    Binding { target: Plan; property: "width"; value: win.width }
    Binding { target: Plan; property: "height"; value: win.height }

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }

    FocusScope {
        id: root
        anchors.fill: parent
        focus: true

        property bool selecting: Greeter.selectedUser !== ""
        property bool multi: Users.count > 1

        // ---- Wallpaper, its blurred twin (for the glass), the scrims ------
        Image {
            id: wall
            anchors.fill: parent
            source: Greeter.wallpaper
            fillMode: Image.PreserveAspectCrop
            asynchronous: false
            cache: false
            sourceSize: Qt.size(win.width, win.height)
            visible: status === Image.Ready
        }
        MultiEffect {
            id: blurred
            anchors.fill: parent
            source: wall
            visible: false
            blurEnabled: true
            blur: 1.0
            blurMax: Style.blurRadius
            saturation: 0.15
            brightness: Style.dark ? -0.04 : 0.02
        }
        // Behind the clock: darkens (dark) or lightens (light) the top band,
        // fading out before the wordmark so the wordmark keeps its glow.
        Rectangle {
            x: 0; y: 0; width: parent.width; height: Plan.wordmark.top
            gradient: Gradient {
                GradientStop { position: 0.0; color: Style.scrim }
                GradientStop { position: Math.min(0.96, (Plan.clockBlock.bottom + 12 * Plan.s) / Math.max(1, Plan.wordmark.top)); color: Style.scrim }
                GradientStop { position: 1.0; color: Qt.rgba(Style.scrim.r, Style.scrim.g, Style.scrim.b, 0) }
            }
        }
        Rectangle {
            x: 0; y: Plan.wordmark.bottom; width: parent.width; height: parent.height - Plan.wordmark.bottom
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(Style.scrim.r, Style.scrim.g, Style.scrim.b, 0) }
                GradientStop { position: Math.min(0.9, (Plan.userBlock.top - Plan.wordmark.bottom) / Math.max(1, root.height - Plan.wordmark.bottom)); color: Style.scrim }
                GradientStop { position: 1.0; color: Style.scrim }
            }
        }

        // ---- Click away from a selected user -------------------------------
        MouseArea {
            anchors.fill: parent
            onClicked: if (root.selecting && root.multi) root.goBack()
        }

        // ---- Appear: fade and a small upward settle -----------------------
        property real appear: Style.reduceMotion ? 1 : 0
        Component.onCompleted: { if (!Style.reduceMotion) appearAnim.start(); }
        NumberAnimation { id: appearAnim; target: root; property: "appear"; from: 0; to: 1; duration: 300; easing.type: Easing.OutCubic }

        // ---- Clock ---------------------------------------------------------
        ClockBlock {
            id: clock
            x: Plan.clockBlock.x; y: Plan.clockBlock.y + (1 - root.appear) * 14 * Plan.s
            width: Plan.clockBlock.width; height: Plan.clockBlock.height
            opacity: root.appear
        }

        // ---- Users ---------------------------------------------------------
        Item {
            id: users
            x: Plan.userBlock.x; y: Plan.userBlock.y + (1 - root.appear) * 14 * Plan.s
            width: Plan.userBlock.width; height: Plan.userBlock.height
            opacity: root.appear
            visible: Users.count > 0 && (Greeter.phase !== 5 || root.selecting)

            readonly property real cell: Math.max(Plan.avatar + 56 * Plan.s, 150 * Plan.s)
            readonly property real gap: 20 * Plan.s
            readonly property real fieldY: Plan.fieldTop

            function rowWidth() { return rep.count * cell + Math.max(0, rep.count - 1) * gap; }

            Repeater {
                id: rep
                model: Users
                delegate: UserTile {
                    id: tile
                    username: model.username
                    displayName: model.displayName
                    iconSource: model.iconSource
                    initials: model.initials
                    colorA: model.colorA
                    colorB: model.colorB
                    readonly property int slotIndex: index
                    diameter: Plan.avatar
                    selected: Greeter.selectedUser === username
                    readonly property bool others: root.selecting && !selected
                    // Natural slot in the row, and the centre slot when selected.
                    readonly property real slotX: (users.width - users.rowWidth()) / 2 + slotIndex * (users.cell + users.gap)
                    x: selected ? (users.width - width) / 2 : slotX
                    y: 0
                    opacity: others ? 0 : 1
                    scale: others ? 0.8 : 1
                    enabled: !others
                    Behavior on x { enabled: !Style.reduceMotion; SpringAnimation { spring: 3.2; damping: 0.34; epsilon: 0.3 } }
                    Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 220 } }
                    Behavior on scale { enabled: !Style.reduceMotion; NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                    onActivated: root.choose(model.username)
                    KeyNavigation.priority: KeyNavigation.BeforeItem
                    Keys.onLeftPressed: root.moveFocus(slotIndex, -1)
                    Keys.onRightPressed: root.moveFocus(slotIndex, 1)
                    Component.onCompleted: if (slotIndex === 0 && !root.selecting) forceActiveFocus()
                }
            }

            // The pill and its message sit under the selected avatar.
            Item {
                id: entry
                width: parent.width
                y: users.fieldY
                height: Plan.fieldH + 10 * Plan.s + 20 * Plan.s
                opacity: root.selecting ? 1 : 0
                visible: opacity > 0.01
                Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 220 } }
                property real lift: root.selecting ? 0 : -10 * Plan.s
                Behavior on lift { enabled: !Style.reduceMotion; SpringAnimation { spring: 3.2; damping: 0.4; epsilon: 0.2 } }
                transform: Translate { y: entry.lift }

                PasswordField {
                    id: field
                    blurred: blurred
                    width: Plan.fieldW; height: Plan.fieldH
                    x: (parent.width - width) / 2
                    onSubmitted: function (t) { Greeter.submit(t); }
                    Component.onCompleted: root.field = field
                }
                Row {
                    id: msg
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: field.bottom
                    anchors.topMargin: 10 * Plan.s
                    spacing: 6 * Plan.s
                    opacity: Greeter.message !== "" ? 1 : 0
                    Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 180 } }
                    Accessible.role: Accessible.StaticText
                    Accessible.name: Greeter.message
                    // An exclamation mark in a circle: the error is not colour alone.
                    Rectangle {
                        visible: Greeter.messageKind === "error"
                        width: 15 * Plan.s; height: width; radius: width / 2
                        anchors.verticalCenter: parent.verticalCenter
                        color: "transparent"
                        border.width: Math.max(1, 1.2 * Plan.s); border.color: Style.text
                        Text {
                            anchors.centerIn: parent
                            text: "!"
                            color: Style.text
                            font.family: "Inter"; font.weight: Font.Bold
                            font.pixelSize: 10 * Plan.s
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: Greeter.message
                        color: Style.text
                        font.family: "Inter"; font.weight: Font.Medium
                        font.pixelSize: 13 * Plan.s
                    }
                }
            }
        }

        // ---- Bottom bar ----------------------------------------------------
        Item {
            id: bottom
            x: Plan.bottomBar.x; y: Plan.bottomBar.y
            width: Plan.bottomBar.width; height: Plan.bottomBar.height
            opacity: root.appear
            Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: Greeter.hostname
                color: Style.textSecondary
                font.family: "Inter"; font.weight: Font.Medium
                font.pixelSize: 13 * Plan.s
                font.letterSpacing: 13 * Plan.s * 0.01
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("This computer is called %1").arg(text)
            }
            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12 * Plan.s
                PowerButton {
                    blurred: blurred
                    action: "sleep"; label: qsTr("Sleep"); confirmText: qsTr("Sleep?")
                    description: qsTr("Suspend this computer")
                }
                PowerButton {
                    blurred: blurred
                    action: "restart"; label: qsTr("Restart"); confirmText: qsTr("Restart?")
                    description: qsTr("Restart this computer")
                }
                PowerButton {
                    blurred: blurred
                    action: "shutdown"; label: qsTr("Shut Down"); confirmText: qsTr("Shut Down?")
                    description: qsTr("Turn this computer off")
                }
            }
        }

        // ---- Fatal problem: plain words, never a black screen -------------
        Item {
            id: fatal
            anchors.fill: parent
            visible: Greeter.phase === 5 || Users.count === 0
            Item {
                id: card
                width: Math.min(parent.width * 0.8, 640 * Plan.s)
                height: fatalCol.implicitHeight + 56 * Plan.s
                x: (parent.width - width) / 2
                y: Plan.userBlock.y
                GlassPanel { anchors.fill: parent; blurred: blurred; radius: 22 * Plan.s }
                Column {
                    id: fatalCol
                    anchors.centerIn: parent
                    width: parent.width - 56 * Plan.s
                    spacing: 8 * Plan.s
                    Text {
                        width: parent.width
                        text: Greeter.phase === 5 ? qsTr("Sign in is not available") : qsTr("No one to sign in")
                        color: Style.text
                        horizontalAlignment: Text.AlignHCenter
                        font.family: "Inter"; font.weight: Font.DemiBold; font.pixelSize: 22 * Plan.s
                        Accessible.role: Accessible.StaticText
                    }
                    Text {
                        width: parent.width
                        text: Greeter.phase === 5 ? Greeter.message : qsTr("No account with a login shell was found on this computer.")
                        color: Style.text
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                        font.family: "Inter"; font.weight: Font.Normal; font.pixelSize: 16 * Plan.s
                        Accessible.role: Accessible.StaticText
                    }
                }
            }
        }

        // ---- Leaving: fade to black, so the hand-over to the desktop is a
        // dissolve through black and never a cut to the bare console.
        Rectangle {
            id: curtain
            anchors.fill: parent
            z: 1000
            color: "black"
            opacity: Greeter.phase === 4 ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 240; easing.type: Easing.InOutQuad } }
        }

        // ---- Behaviour -----------------------------------------------------
        property Item field: null
        function choose(name) {
            Greeter.select(name);
            if (field) field.focusInput();
        }
        function goBack() {
            var i = Users.indexOfUser(Greeter.selectedUser);
            if (field) field.clear();
            Greeter.back();
            if (i >= 0 && rep.itemAt(i)) rep.itemAt(i).forceActiveFocus();
        }
        function moveFocus(i, d) {
            var n = i + d;
            if (n >= 0 && n < rep.count) rep.itemAt(n).forceActiveFocus();
        }
        Keys.onEscapePressed: {
            if (!selecting) return;
            if (multi) goBack();
            else { if (field) field.clear(); }
        }
        // Typing anywhere with one selected user goes to the field.
        Connections {
            target: Greeter
            function onInputReady() { if (root.field) { root.field.nextPrompt(); root.field.focusInput(); } }
            function onFailed() { if (root.field) root.field.playFailure(); }
        }
        Connections {
            target: Greeter
            function onSelectedUserChanged() { if (Greeter.selectedUser !== "" && root.field) root.field.focusInput(); }
        }
        Component.onDestruction: {}

        // Cursor: hidden after 10 s without movement.
        property bool cursorIdle: false
        Timer { id: idleTimer; interval: 10000; running: true; onTriggered: root.cursorIdle = true }
        HoverHandler {
            cursorShape: root.cursorIdle ? Qt.BlankCursor : Qt.ArrowCursor
            onPointChanged: { root.cursorIdle = false; idleTimer.restart(); }
        }
    }
}
