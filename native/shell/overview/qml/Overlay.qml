// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Window
import Taos.Overview

// The overlay root: scrim, the switcher or the overview, key handling, open and close motion.
FocusScope {
    id: root
    focus: true
    readonly property string mode: Overview.mode
    readonly property bool open: mode !== "hidden"
    property real progress: 0
    // the mode shown now; kept while fading out so the content does not change under the fade
    property string shownMode: "switcher"
    readonly property string font: Style.fontStack

    Accessible.role: Accessible.Pane
    Accessible.name: shownMode === "overview" ? qsTr("Window overview") : shownMode === "help" ? qsTr("Keyboard shortcuts") : qsTr("Window switcher")
    Component.onCompleted: Overview.setAccessibleTarget(root)

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }

    onModeChanged: if (mode !== "hidden") shownMode = mode
    onOpenChanged: {
        if (open) { forceActiveFocus(); if (Overview.framed) animateTo(1); }
        else animateTo(0);
    }
    Connections {
        target: Overview
        function onFramedChanged() { if (Overview.framed && root.open) { root.forceActiveFocus(); root.animateTo(1); } }
    }
    function animateTo(v) {
        fade.stop();
        if (Style.reduceMotion) {
            progress = v;
            if (v === 0) Overview.closeFinished();
            return;
        }
        fade.to = v;
        fade.duration = 200;
        fade.easing.type = v > progress ? Easing.OutCubic : Easing.InOutCubic;
        fade.start();
    }
    NumberAnimation {
        id: fade
        target: root
        property: "progress"
        onFinished: if (root.progress <= 0.001) Overview.closeFinished()
    }

    // dims the desktop a little in the overview; the switcher leaves it alone
    Rectangle {
        anchors.fill: parent
        color: Style.ground
        opacity: root.shownMode === "overview" || root.shownMode === "help" ? root.progress * (Style.dark ? 0.55 : 0.72) : 0
    }
    MouseArea {
        anchors.fill: parent
        onClicked: Overview.cancel()
        Accessible.ignored: true
    }
    Switcher {
        anchors.fill: parent
        visible: root.shownMode === "switcher"
        progress: root.progress
    }
    OverviewView {
        id: overviewView
        anchors.fill: parent
        visible: root.shownMode === "overview"
        progress: root.progress
    }

    HelpView {
        id: helpView
        anchors.fill: parent
        visible: root.shownMode === "help"
        progress: root.progress
    }

    Keys.onPressed: (e) => {
        const mod = e.modifiers & ~Qt.KeypadModifier;
        const noMods = (mod & ~Qt.AltModifier) === 0;
        switch (e.key) {
        case Qt.Key_Up:       if (root.mode === "help") { helpView.scrollBy(-80); e.accepted = true; return; } Overview.move("up"); e.accepted = true; return;
        case Qt.Key_Down:     if (root.mode === "help") { helpView.scrollBy(80); e.accepted = true; return; } Overview.move("down"); e.accepted = true; return;
        case Qt.Key_PageUp:   if (root.mode === "help") { helpView.scrollBy(-400); e.accepted = true; return; } break;
        case Qt.Key_PageDown: if (root.mode === "help") { helpView.scrollBy(400); e.accepted = true; return; } break;
        case Qt.Key_Home:     if (root.mode === "help") { helpView.scrollTo(true); e.accepted = true; return; } break;
        case Qt.Key_End:      if (root.mode === "help") { helpView.scrollTo(false); e.accepted = true; return; } break;
        case Qt.Key_Tab:      Overview.move((e.modifiers & Qt.ShiftModifier) ? "prev" : "next"); e.accepted = true; return;
        case Qt.Key_Backtab:  Overview.move("prev"); e.accepted = true; return;
        case Qt.Key_Left:     Overview.move("left"); e.accepted = true; return;
        case Qt.Key_Right:    Overview.move("right"); e.accepted = true; return;
        case Qt.Key_Return:
        case Qt.Key_Enter:    if (root.mode === "help") Overview.cancel(); else Overview.commit(); e.accepted = true; return;
        case Qt.Key_Escape:
            if (Overview.filterText.length > 0) Overview.clearFilter(); else Overview.cancel();
            e.accepted = true; return;
        case Qt.Key_Backspace: Overview.backspace(); e.accepted = true; return;
        default: break;
        }
        if (root.mode === "overview") {
            const ctrl = (e.modifiers & Qt.ControlModifier) !== 0;
            if (ctrl && e.key === Qt.Key_Left) { Overview.stepSpace(-1); e.accepted = true; return; }
            if (ctrl && e.key === Qt.Key_Right) { Overview.stepSpace(1); e.accepted = true; return; }
            if (ctrl && e.key === Qt.Key_N) { Overview.addSpace(); e.accepted = true; return; }
            if (e.key === Qt.Key_F2) { overviewView.beginRename(""); e.accepted = true; return; }
            // a digit jumps to that desktop while the search is empty; with text in the search it is part of the text
            if (!ctrl && noMods && Overview.filterText.length === 0 && e.key >= Qt.Key_1 && e.key <= Qt.Key_9) {
                Overview.jumpToSpace(e.key - Qt.Key_0); e.accepted = true; return;
            }
        }
        if (root.mode === "switcher" && e.key === Qt.Key_Q && noMods) { Overview.closeSelected(); e.accepted = true; return; }
        if ((root.mode === "overview" || root.mode === "help") && noMods && e.text.length > 0 && e.text.charCodeAt(0) >= 32 && e.text.charCodeAt(0) !== 127) {
            Overview.typeText(e.text); e.accepted = true;
        }
    }
    Keys.onReleased: (e) => {
        if ((e.key === Qt.Key_Alt) && root.mode === "switcher" && !Overview.sticky) {
            Overview.commit(); e.accepted = true;
        }
    }
}
