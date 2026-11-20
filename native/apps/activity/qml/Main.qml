import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Taos 1.0
import Taos.Style 1.0
import Taos.Activity 1.0

// Activity (registry id `dashboard`): a thin face over ActivityEngine and
// ModelActivityFeed. All data comes from the controller through the
// foundation; the only state kept here is view state (scroll, expanded cards,
// the three feed filters).
TaosWindow {
    id: win
    width: 1180; height: 820
    minimumWidth: 340; minimumHeight: 480
    title: qsTr("Activity")
    readonly property bool phone: width < 640
    readonly property bool signedIn: Taos.session.state === Session.SignedIn
    // Polling and the stream run only while the window can be seen.
    readonly property bool seen: visible && visibility !== Window.Minimized && visibility !== Window.Hidden
    readonly property bool admin: signedIn && Taos.session.user && Taos.session.user.is_admin === true

    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    palette.window: TaosStyle.ground
    palette.windowText: TaosStyle.text
    palette.text: TaosStyle.text
    palette.base: TaosStyle.surface

    ActivityEngine { id: engine; objectName: "engine"; active: win.seen && win.signedIn }
    ModelActivityFeed { id: theFeed; objectName: "feed"; active: engine.active }
    // Navigation for the phone layout (also used by the screenshot harness).
    function showProcesses() { if (nav.depth === 1) nav.push(processesPage) }
    function showModelActivity() { if (nav.depth === 1) nav.push(activityPage) }
    function back() { if (nav.depth > 1) nav.pop() }

    // Refresh when the window becomes the active one (the web app's focus hook).
    onActiveChanged: if (active && engine.active) engine.refresh()

    // Ground: a faint accent wash so the glass cards read as glass.
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.tint(TaosStyle.ground, Pal.alpha(Pal.accent, 0.10)) }
            GradientStop { position: 0.55; color: TaosStyle.ground }
            GradientStop { position: 1.0; color: Qt.tint(TaosStyle.ground, Pal.alpha(Pal.info, 0.06)) }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        spacing: 0
        visible: win.signedIn

        // Header
        Item {
            Layout.fillWidth: true
            implicitHeight: win.phone ? 60 : 72
            RowLayout {
                anchors { fill: parent; leftMargin: win.phone ? 14 : 22; rightMargin: win.phone ? 10 : 18 }
                spacing: 10
                ColumnLayout {
                    spacing: 1
                    Txt { text: qsTr("Activity"); font.pixelSize: win.phone ? 20 : 24; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
                    Txt {
                        visible: engine.phase === ActivityEngine.Ready
                        text: Fmt.systemLine(engine.hardware)
                        font.pixelSize: 12; color: TaosStyle.textTertiary
                        Layout.maximumWidth: win.width - (win.phone ? 150 : 420)
                    }
                }
                Item { Layout.fillWidth: true }
                GlassButton {
                    visible: win.admin && !win.phone
                    text: qsTr("Restart AI services")
                    Accessible.name: qsTr("Restart AI services")
                    onClicked: engine.askRestart()
                }
                GlassButton {
                    visible: win.admin && win.phone
                    text: "⚡"
                    implicitWidth: 36; leftPadding: 0; rightPadding: 0
                    Accessible.name: qsTr("Restart AI services")
                    onClicked: engine.askRestart()
                }
                GlassButton {
                    text: "↻"
                    implicitWidth: 36; leftPadding: 0; rightPadding: 0
                    Accessible.name: qsTr("Refresh")
                    onClicked: engine.refresh()
                }
            }
        }

        StackView {
            id: nav
            Layout.fillWidth: true
            Layout.fillHeight: true
            initialItem: main
            pushEnter: Transition { NumberAnimation { property: "x"; from: nav.width * 0.25; to: 0; duration: Pal.dur(220); easing.type: Easing.OutCubic }
                                    NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.dur(220) } }
            pushExit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Pal.dur(160) } }
            popEnter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.dur(200) } }
            popExit: Transition { NumberAnimation { property: "x"; to: nav.width * 0.25; duration: Pal.dur(200) }
                                  NumberAnimation { property: "opacity"; to: 0; duration: Pal.dur(200) } }
            // Leave the pushed page behind when the window grows out of phone size.
            Connections { target: win; function onPhoneChanged() { if (!win.phone && nav.depth > 1) nav.pop(null) } }
        }
    }

    Component {
        id: main
        Item {
            // First load: a spinner. Never succeeded: the unavailable state.
            BusyIndicator {
                anchors.centerIn: parent
                running: engine.phase === ActivityEngine.Loading
                visible: running
                Accessible.name: qsTr("Loading activity")
            }
            ColumnLayout {
                anchors.centerIn: parent
                visible: engine.phase === ActivityEngine.Unavailable
                spacing: 8
                Txt { text: qsTr("Activity data unavailable"); font.pixelSize: 16; font.weight: Font.Medium; color: TaosStyle.textSecondary; Layout.alignment: Qt.AlignHCenter; Accessible.role: Accessible.AlertMessage }
                Txt { visible: text.length > 0; text: engine.errorText; font.pixelSize: 12; color: TaosStyle.textTertiary; Layout.alignment: Qt.AlignHCenter }
                GlassButton { text: qsTr("Try again"); Layout.alignment: Qt.AlignHCenter; onClicked: engine.refresh() }
            }
            Dashboard {
                anchors.fill: parent
                visible: engine.phase === ActivityEngine.Ready
                eng: engine; feed: theFeed; phone: win.phone
                onOpenProcesses: nav.push(processesPage)
                onOpenModelActivity: nav.push(activityPage)
            }
        }
    }

    Component {
        id: processesPage
        Item {
            ColumnLayout {
                anchors { fill: parent; margins: 12 }
                spacing: 8
                GlassButton { text: "‹ " + qsTr("Back"); Accessible.name: qsTr("Back"); onClicked: nav.pop() }
                Txt { text: qsTr("Top Processes"); font.pixelSize: 20; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
                Flickable {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentHeight: pl.implicitHeight; clip: true
                    ProcessesList { id: pl; width: parent.width; processes: engine.activity.processes || [] }
                }
            }
        }
    }
    Component {
        id: activityPage
        Item {
            ColumnLayout {
                anchors { fill: parent; margins: 12 }
                spacing: 8
                GlassButton { text: "‹ " + qsTr("Back"); Accessible.name: qsTr("Back"); onClicked: nav.pop() }
                Txt { text: qsTr("Model Activity"); font.pixelSize: 20; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
                ModelActivityPanel { feed: theFeed; nowSec: engine.now; compact: true; listHeight: 480; Layout.fillHeight: true }
            }
        }
    }

    RestartDialog { eng: engine }

    SignIn {
        anchors.fill: parent
        visible: !win.signedIn
    }
}
