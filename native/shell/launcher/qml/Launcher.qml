// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Window
import Taos.Launcher

// The launcher: a centred glass panel in the upper third, a large search field, grouped results.
FocusScope {
    id: root
    focus: true
    readonly property bool open: Launcher.open
    property real progress: 0
    property bool ctrlHeld: false
    // The style (layout.launcher) is a renderer of the one shared menu: centered, anchored (needs the taskbar), ring or text.
    // A narrow screen is a phone: the whole screen, whatever the theme says. `--style` forces one (a phone home screen).
    readonly property bool phone: Launcher.styleOverride === "phone" || (Launcher.styleOverride === "" && width < 640)
    readonly property string styleName: phone ? "phone"
        : Launcher.styleOverride !== "" ? Launcher.styleOverride
        : Style.launcherPlace === "anchored" ? (Style.dockMode === "taskbar" ? "anchored" : "centered") : Style.launcherPlace
    readonly property bool ring: styleName === "ring"
    readonly property bool compact: styleName === "anchored"
    readonly property bool fullScreen: styleName === "phone" || styleName === "text"
    readonly property bool menuOn: Launcher.menuMode && Launcher.menuVisible
    readonly property real tbH: Style.taskbarHeight
    readonly property real panelW: fullScreen ? width : compact ? Math.min(menuOn ? 680 : 380, width - 8) : ring ? Math.min(Math.max(menuOn ? 600 : 520, width * 0.4), width - 32) : Math.min(menuOn ? 920 : 720, width - 32)
    readonly property real searchH: fullScreen ? 64 : compact ? 52 : ring ? 56 : 68
    readonly property real compactH: Math.min(520, height - tbH - 16)
    readonly property real menuH: fullScreen ? height : compact ? Math.min(600, height - tbH - 16) : ring ? Math.min(580, height - 48) : Math.min(700, height - 64)
    readonly property real maxListH: ring ? Math.max(104, height * 0.19) : Math.max(160, Math.min(440, height * 0.5))
    readonly property real listH: Math.min(maxListH, list.contentHeight)
    readonly property real bodyH: menuOn ? Math.max(0, menuH - searchH - 1 - noticeH)
                                  : fullScreen ? Math.max(0, height - searchH - 1 - noticeH)
                                  : compact ? Math.max(0, compactH - searchH - 1 - noticeH) : Launcher.actionsOpen ? actions.implicitHeight
                                  : (Launcher.count > 0 ? listH + 8 : (noResults.visible ? 76 : 0))
    readonly property real noticeH: Launcher.notice.length > 0 ? 38 : 0
    readonly property real targetH: menuOn ? menuH : fullScreen ? height : compact ? compactH : searchH + (bodyH > 0 ? bodyH + 1 : 0) + noticeH
    onStyleNameChanged: if (Launcher.menuMode) Launcher.menu.setLayout(styleName)

    Accessible.role: Accessible.Pane
    Accessible.name: qsTr("Launcher")
    Component.onCompleted: Launcher.setAccessibleTarget(root)

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }
    function setSearchText(t) { input.text = t; }   // for the offscreen renders

    onOpenChanged: {
        if (open) { input.forceActiveFocus(); animateTo(1); }
        else animateTo(0);
    }
    function animateTo(v) {
        fade.stop();
        if (Style.reduceMotion) {
            progress = v;
            if (v === 0) Launcher.closeFinished();
            return;
        }
        fade.to = v;
        fade.duration = v > progress ? 160 : 130;
        fade.easing.type = v > progress ? Easing.OutCubic : Easing.InCubic;
        fade.start();
    }
    NumberAnimation {
        id: fade
        target: root
        property: "progress"
        onFinished: if (root.progress <= 0.001) Launcher.closeFinished()
    }

    // a slight dim over the desktop; a click on it closes the launcher
    Rectangle {
        anchors.fill: parent
        color: Style.scrim
        opacity: root.progress * 0.5
    }
    MouseArea {
        anchors.fill: parent
        onClicked: Launcher.hide()
        Accessible.ignored: true
    }

    Item {
        id: panel
        width: root.panelW
        height: root.targetH
        x: root.fullScreen ? 0 : root.compact ? 4 : Math.round((root.width - width) / 2)
        y: root.fullScreen ? 0
           : root.compact ? (Style.dockEdge === "top" ? root.tbH + 4 : root.height - root.tbH - 4 - height)
           : root.menuOn ? Math.max(16, Math.round((root.height - height) / 2))
           : root.ring ? Math.round(root.height * 0.60) : Math.round(root.height * 0.17)
        opacity: root.progress
        scale: 0.98 + 0.02 * root.progress
        transformOrigin: root.compact ? (Style.dockEdge === "top" ? Item.TopLeft : Item.BottomLeft) : Item.Top
        Behavior on height {
            enabled: root.open && !Style.reduceMotion
            NumberAnimation { duration: 230; easing.type: Easing.OutQuint }
        }

        Glass { anchors.fill: parent; radius: root.fullScreen ? 0 : Math.round((22) * Style.radiusScale); kind: root.fullScreen ? "solid" : Style.dockSurface }
        MouseArea { anchors.fill: parent; Accessible.ignored: true }   // clicks on the panel do not reach the backdrop

        // ------------------------------------------------ search field
        Item {
            id: bar
            width: parent.width
            height: root.searchH

            // a magnifier drawn from two shapes so it follows the text colour
            Item {
                id: glass
                x: 24
                anchors.verticalCenter: parent.verticalCenter
                width: 22; height: 22
                Accessible.ignored: true
                Rectangle {
                    visible: !root.ring
                    width: 15; height: 15; radius: 8
                    color: "transparent"
                    border.width: 2
                    border.color: Style.textSecondary
                }
                Rectangle {
                    visible: !root.ring
                    x: 12; y: 15
                    width: 9; height: 2.4; radius: 1.2
                    color: Style.textSecondary
                    transformOrigin: Item.TopLeft
                    rotation: 45
                }
                // the ring's reticle: two circles and four ticks
                Rectangle { visible: root.ring; anchors.centerIn: parent; width: 20; height: 20; radius: 10; color: "transparent"; border.width: 1.5; border.color: Style.accent }
                Rectangle { visible: root.ring; anchors.centerIn: parent; width: 9; height: 9; radius: 4.5; color: "transparent"; border.width: 1.5; border.color: Style.accent }
                Rectangle { visible: root.ring; x: 10.25; y: -2; width: 1.5; height: 5; color: Style.accent }
                Rectangle { visible: root.ring; x: 10.25; y: 19; width: 1.5; height: 5; color: Style.accent }
                Rectangle { visible: root.ring; x: -2; y: 10.25; width: 5; height: 1.5; color: Style.accent }
                Rectangle { visible: root.ring; x: 19; y: 10.25; width: 5; height: 1.5; color: Style.accent }
            }

            TextInput {
                id: input
                anchors.left: glass.right
                anchors.leftMargin: 14
                anchors.right: escHint.left
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                height: root.compact ? 28 : 34
                verticalAlignment: TextInput.AlignVCenter
                color: Style.text
                selectionColor: Style.selection
                selectedTextColor: Style.text
                font.family: root.ring ? Style.fontDataStack : Style.fontStack
                font.pixelSize: root.ring ? 15 : root.compact ? 17 : 24
                font.weight: Font.Normal
                clip: true
                focus: true
                selectByMouse: true
                inputMethodHints: Qt.ImhNoPredictiveText
                Accessible.role: Accessible.EditableText
                Accessible.name: qsTr("Search or ask taOS")
                Accessible.description: qsTr("Type to search apps, windows, actions and calculate. Arrow keys choose a result, Enter opens it, Tab shows more actions.")
                onTextChanged: Launcher.setQuery(text)

                Text {
                    visible: input.text.length === 0 && !input.inputMethodComposing
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    text: qsTr("Search or ask taOS")
                    color: Style.textTertiary
                    font: input.font
                    Accessible.ignored: true
                }
                cursorDelegate: Rectangle {
                    id: caret
                    width: 2
                    radius: 1
                    color: Style.accent
                    visible: input.activeFocus
                    SequentialAnimation on opacity {
                        running: caret.visible && !Style.reduceMotion
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.25; duration: 560; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1.0; duration: 560; easing.type: Easing.InOutSine }
                    }
                }

                Keys.onPressed: (e) => {
                    const ctrl = (e.modifiers & Qt.ControlModifier) !== 0;
                    if (e.key === Qt.Key_Control) root.ctrlHeld = true;
                    if (root.menuOn) {
                        // the menu is showing: arrows walk it, Enter opens, Tab or Menu shows what else can be done, and
                        // typing (anything else) goes to the search field and takes the screen over
                        const bare = input.text.length === 0 || (Launcher.actionsMode && input.text === ">");
                        if (e.key === Qt.Key_Up) { Launcher.menuMove(0, -1); e.accepted = true; return; }
                        if (e.key === Qt.Key_Down) { Launcher.menuMove(0, 1); e.accepted = true; return; }
                        if (e.key === Qt.Key_Left && bare) { if (Launcher.actionsMode) Launcher.menuBack(); else Launcher.menuMove(-1, 0); e.accepted = true; return; }
                        if (e.key === Qt.Key_Right && bare) { if (Launcher.actionsMode) Launcher.menuEnter(); else Launcher.menuMove(1, 0); e.accepted = true; return; }
                        if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) { Launcher.menuEnter(); e.accepted = true; return; }
                        if ((e.key === Qt.Key_Tab || e.key === Qt.Key_Menu) && !Launcher.actionsMode) { Launcher.menuContext(); e.accepted = true; return; }
                        if (e.key === Qt.Key_F10 && (e.modifiers & Qt.ShiftModifier)) { Launcher.menuContext(); e.accepted = true; return; }
                        if (e.key === Qt.Key_Backspace && Launcher.actionsMode && input.text === ">" && Launcher.menu.actionPath.length > 0) { Launcher.menuBack(); e.accepted = true; return; }
                        if (e.key === Qt.Key_Escape) { Launcher.escape(); e.accepted = true; return; }
                    }
                    if (ctrl && e.key >= Qt.Key_1 && e.key <= Qt.Key_9) { Launcher.quickPick(e.key - Qt.Key_0); e.accepted = true; return; }
                    if (ctrl && e.key === Qt.Key_N) { Launcher.move(1); e.accepted = true; return; }
                    if (ctrl && e.key === Qt.Key_P) { Launcher.move(-1); e.accepted = true; return; }
                    switch (e.key) {
                    case Qt.Key_Down: Launcher.move(1); e.accepted = true; return;
                    case Qt.Key_Up: Launcher.move(-1); e.accepted = true; return;
                    case Qt.Key_Tab:
                        if (Launcher.actionsOpen) Launcher.closeActions(); else Launcher.openActions();
                        e.accepted = true; return;
                    case Qt.Key_Backtab: Launcher.closeActions(); e.accepted = true; return;
                    case Qt.Key_Right:
                        if (!Launcher.actionsOpen && input.cursorPosition === input.length && input.selectionStart === input.selectionEnd) {
                            Launcher.openActions(); e.accepted = true;
                        }
                        return;
                    case Qt.Key_Left:
                        if (Launcher.actionsOpen) { Launcher.closeActions(); e.accepted = true; }
                        return;
                    case Qt.Key_Return:
                    case Qt.Key_Enter:
                        if (e.modifiers & Qt.ShiftModifier) Launcher.activateAlternate(); else Launcher.activate();
                        e.accepted = true; return;
                    case Qt.Key_Escape: Launcher.escape(); e.accepted = true; return;
                    default: break;
                    }
                }
                Keys.onReleased: (e) => { if (e.key === Qt.Key_Control) root.ctrlHeld = false; }
            }
            Connections {
                target: Launcher
                function onClearInput() { input.text = ""; }
                function onSetInputText(t) { input.text = t; input.cursorPosition = input.length; }
                function onShowWindow() { input.text = ""; }
            }

            Rectangle {
                id: escHint
                anchors.right: parent.right
                anchors.rightMargin: 20
                anchors.verticalCenter: parent.verticalCenter
                width: escText.implicitWidth + 14
                height: 22
                radius: 6
                opacity: input.text.length > 0 ? 1 : 0
                color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.10 : 0.07)
                Behavior on opacity { NumberAnimation { duration: root.dur(120) } }
                Text {
                    id: escText
                    anchors.centerIn: parent
                    text: qsTr("esc")
                    color: Style.textTertiary
                    font.family: "Inter"
                    font.pixelSize: 11
                    font.weight: Font.Medium
                    Accessible.ignored: true
                }
                MouseArea { anchors.fill: parent; onClicked: Launcher.escape(); Accessible.ignored: true }
            }
        }

        Rectangle {
            id: rule
            y: root.searchH
            width: parent.width
            height: 1
            color: Style.hairline
            opacity: root.bodyH > 0 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: root.dur(120) } }
        }

        // ------------------------------------------------ results
        Item {
            id: body
            y: root.searchH + 1
            width: parent.width
            height: root.bodyH
            clip: true

            ListView {
                id: list
                visible: !Launcher.actionsOpen && !root.menuOn
                anchors.fill: parent
                anchors.topMargin: 0
                model: Launcher.rows
                clip: true
                interactive: contentHeight > height
                boundsBehavior: Flickable.StopAtBounds
                cacheBuffer: 4000
                currentIndex: Launcher.currentIndex
                highlightFollowsCurrentItem: false
                reuseItems: false
                Accessible.role: Accessible.List
                Accessible.name: qsTr("Results")
                onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

                // the selection: one rectangle that slides from row to row
                Rectangle {
                    id: hl
                    parent: list.contentItem
                    z: 0
                    readonly property Item cur: list.currentItem
                    property real ty: 0
                    property real th: 52
                    visible: list.count > 0 && list.currentIndex >= 0
                    x: 8
                    width: list.width - 16
                    y: ty
                    height: th
                    radius: Math.round((13) * Style.radiusScale)
                    color: root.ring ? Qt.rgba(Style.accent.r, Style.accent.g, Style.accent.b, 0.06) : Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.13 : 0.10)
                    border.width: 1
                    border.color: root.ring ? Style.accent : Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.10 : 0.08)
                    Behavior on y { enabled: root.open && !Style.reduceMotion; NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    Behavior on height { enabled: root.open && !Style.reduceMotion; NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    function sync() {
                        if (cur) { ty = cur.y + cur.headerH + 2; th = cur.bodyH - 4; }
                    }
                    onCurChanged: sync()
                    Connections { target: hl.cur; function onYChanged() { hl.sync(); } function onHeightChanged() { hl.sync(); } }
                }

                delegate: ResultRow {
                    z: 1
                    ctrlHeld: root.ctrlHeld
                }

                footer: Item { width: 1; height: 8 }
            }

            // a thin scroll indicator, only when the list overflows
            Rectangle {
                visible: !Launcher.actionsOpen && !root.menuOn && list.contentHeight > list.height + 1
                x: parent.width - 7
                width: 3
                radius: 1.5
                color: Style.scrollbar
                y: 4 + (list.height - 8 - height) * (list.contentY / Math.max(1, list.contentHeight - list.height))
                height: Math.max(24, (list.height - 8) * list.height / list.contentHeight)
            }

            ActionsView {
                id: actions
                visible: Launcher.actionsOpen && !root.menuOn
                width: parent.width
                height: implicitHeight
            }

            // the shared app menu, drawn by the style the theme asked for
            Loader {
                id: menuLoader
                anchors.fill: parent
                active: Launcher.menuMode
                visible: root.menuOn
                sourceComponent: root.styleName === "phone" ? phoneC : root.styleName === "text" ? textC : root.styleName === "ring" ? ringC : root.styleName === "anchored" ? anchoredC : centeredC
                onLoaded: Launcher.menu.setLayout(root.styleName)
            }
            Component { id: centeredC; MenuCentered {} }
            Component { id: anchoredC; MenuAnchored {} }
            Component { id: ringC; MenuRing {} }
            Component { id: textC; MenuText {} }
            Component { id: phoneC; MenuPhone {} }
            Connections { target: menuLoader.item; ignoreUnknownSignals: true; function onRequestText(t) { input.text = t; input.cursorPosition = input.length } }

            Item {
                id: noResults
                visible: !Launcher.actionsOpen && Launcher.count === 0 && !Launcher.searching && Launcher.query.length > 0
                anchors.fill: parent
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("No results")
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Nothing matches “%1”").arg(Launcher.query)
                    color: Style.textSecondary
                    font.family: "Inter"
                    font.pixelSize: 15
                    elide: Text.ElideRight
                    width: parent.width - 48
                    horizontalAlignment: Text.AlignHCenter
                    Accessible.ignored: true
                }
            }
        }

        Loader { anchors.fill: parent; active: Launcher.menuMode; z: 10; sourceComponent: ContextSheet {} }

        // ------------------------------------------------ notice (an action failed, a stub)
        Item {
            id: notice
            y: parent.height - root.noticeH
            width: parent.width
            height: root.noticeH
            visible: root.noticeH > 0
            clip: true
            Rectangle { width: parent.width; height: 1; color: Style.hairline }
            Text {
                anchors.centerIn: parent
                width: parent.width - 44
                horizontalAlignment: Text.AlignHCenter
                text: Launcher.notice
                color: Style.warning
                elide: Text.ElideRight
                font.family: "Inter"
                font.pixelSize: 13
                Accessible.role: Accessible.AlertMessage
                Accessible.name: Launcher.notice
            }
        }
    }
}
