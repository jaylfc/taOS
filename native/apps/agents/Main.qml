// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Data 1.0
import Taos.Style 1.0
import Taos.Apps 1.0

// Agents: the agents deployed for you, where each one runs and what state it is
// in. A face over the AgentDirectory engine; everything shown is the
// controller's state, fetched through the foundation client and refreshed while
// the window is up. Operate-mode UI: tokens only, words for every state.
TaosWindow {
    id: win
    width: 1040
    height: 700
    minimumWidth: 360
    minimumHeight: 440
    title: qsTr("Agents")
    objectName: "agentsWindow"

    readonly property bool signedIn: Taos.session.state === Session.SignedIn
    readonly property bool narrow: width < 720
    property alias dir: dir
    property string selected: ""
    property string pendingOpen: ""
    property string toastText: ""
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    AgentDirectory {
        id: dir
        objectName: "directory"
        session: Taos.session
        running: win.signedIn && win.visible
        onLaunchFailed: (text) => win.showToast(text)
        onStateChanged: win.resolvePending()
    }

    function showToast(text) { toastText = text; toastTimer.restart() }
    Timer { id: toastTimer; interval: 4500; onTriggered: win.toastText = "" }

    // --agent <name> or --agent=<name>, on start and on a second launch
    function agentFrom(args) {
        for (let i = 0; i < args.length; i++) {
            if (args[i] === "--agent" && i + 1 < args.length) return args[i + 1]
            if (args[i].indexOf("--agent=") === 0) return args[i].substring(8)
        }
        return ""
    }
    function open(name) {
        if (name === "") return
        pendingOpen = name
        resolvePending()
    }
    function resolvePending() {
        if (pendingOpen === "") return
        if (pendingOpen === "taos-agent" || dir.agent(pendingOpen).name !== undefined) {
            selected = pendingOpen; pendingOpen = ""
        } else if (dir.loaded) {
            showToast(qsTr("No agent named %1 in your list.").arg(pendingOpen)); pendingOpen = ""
        }
    }
    Component.onCompleted: open(agentFrom(Taos.arguments))
    Connections {
        target: Taos
        function onActivated(args) { win.open(win.agentFrom(args)); win.raise(); win.requestActivate() }
    }

    Shortcut { sequences: ["Ctrl+F", "/"]; onActivated: search.forceActiveFocus() }
    Shortcut { sequences: ["Ctrl+R", "F5"]; onActivated: dir.refresh() }
    Shortcut { sequence: "Escape"; enabled: win.narrow && win.selected !== ""; onActivated: win.selected = "" }

    SignInForm { anchors.fill: parent; visible: !win.signedIn; appName: qsTr("Agents") }

    RowLayout {
        anchors.fill: parent
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        visible: win.signedIn
        spacing: 0

        // ---- list column ----------------------------------------------------------
        ColumnLayout {
            Layout.fillHeight: true
            Layout.fillWidth: win.narrow
            Layout.preferredWidth: win.narrow ? -1 : 400
            visible: !win.narrow || win.selected === ""
            spacing: 0

            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: 16
                Layout.bottomMargin: 8
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    TaosText {
                        text: qsTr("Agents"); font.pixelSize: Look.heading; font.weight: Font.DemiBold
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    TaosText {
                        visible: dir.loaded
                        text: qsTr("%1 deployed").arg(dir.totalCount)
                        color: Look.textSecondary; font.pixelSize: Look.small; font.features: { "tnum": 1 }
                        Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 2
                    }
                    Item { Layout.fillWidth: true }
                    Chip { objectName: "pausedChip"; visible: dir.loaded && !dir.live; text: qsTr("paused")
                           tone: "warning"; accessibleName: qsTr("live updates paused") }
                    TaosButton { objectName: "refreshButton"; text: qsTr("Refresh"); compact: true; variant: "ghost"
                                 accessibleName: qsTr("Refresh the agent list"); onClicked: dir.refresh() }
                }
                TaosField {
                    id: search
                    objectName: "searchField"
                    Layout.fillWidth: true
                    placeholderText: qsTr("Search by name, framework, model or device")
                    accessibleName: qsTr("Search agents")
                    visible: dir.totalCount > 0
                    onTextChanged: dir.filterText = text
                    Keys.onDownPressed: list.forceActiveFocus()
                    Keys.onEscapePressed: { text = ""; list.forceActiveFocus() }
                }
                Flow {
                    id: filters
                    Layout.fillWidth: true
                    spacing: 6
                    visible: dir.totalCount > 0
                    component Pill: C.AbstractButton {
                        id: pill
                        implicitHeight: 24
                        implicitWidth: lbl.implicitWidth + 20
                        hoverEnabled: true
                        focusPolicy: Qt.StrongFocus
                        Accessible.role: Accessible.CheckBox
                        Accessible.name: text
                        Accessible.checked: checked
                        background: Rectangle {
                            radius: 12
                            color: pill.checked ? Look.selection : pill.hovered ? Look.hover : Look.field
                            border.width: pill.visualFocus ? 2 : 1
                            border.color: pill.visualFocus ? Look.focusRing : pill.checked ? Look.accent : Look.hairline
                            Behavior on color { ColorAnimation { duration: Look.fast } }
                        }
                        contentItem: TaosText { id: lbl; text: pill.text; horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter; font.pixelSize: Look.small
                                                font.weight: Font.Medium; color: pill.checked ? Look.text : Look.textSecondary }
                    }
                    Pill { objectName: "stateAll"; text: qsTr("All"); checked: dir.stateFilter === ""; onClicked: dir.stateFilter = "" }
                    Repeater {
                        model: ["running", "paused", "stopped", "error"]
                        delegate: Pill {
                            required property string modelData
                            readonly property int n: dir.stateCounts[modelData] || 0
                            visible: n > 0 || dir.stateFilter === modelData
                            objectName: "state_" + modelData
                            text: Tone.stateLabel(modelData) + " " + n
                            checked: dir.stateFilter === modelData
                            onClicked: dir.stateFilter = dir.stateFilter === modelData ? "" : modelData
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: dir.totalCount > 0 && dir.devices.length > 1
                    spacing: 8
                    TaosCombo {
                        id: deviceCombo
                        objectName: "deviceFilter"
                        Layout.fillWidth: true
                        implicitHeight: 28
                        accessibleName: qsTr("Filter by device")
                        textRole: "label"
                        model: [{ label: qsTr("All devices"), value: "" }].concat(dir.devices.map(d => ({
                            label: d.label + (d.reach === "offline" ? " " + qsTr("(offline)") : "") + " (" + d.count + ")", value: d.value })))
                        onActivated: (i) => dir.deviceFilter = model[i].value
                    }
                    Pill { objectName: "groupToggle"; text: qsTr("Group by device"); checked: dir.groupByDevice
                           onClicked: dir.groupByDevice = !dir.groupByDevice }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }

            // the built-in agent, pinned
            AgentRow {
                id: taosRow
                objectName: "taosAgentRow"
                Layout.fillWidth: true
                visible: dir.loaded && dir.filterText === "" && dir.stateFilter === "" && dir.deviceFilter === ""
                height: visible ? 74 : 0
                agentName: "taos-agent"
                title: qsTr("taOS Agent")
                subtitle: qsTr("Built in") + (dir.taosAgent.model ? " · " + dir.taosAgent.model : "")
                agentState: "running"
                deviceName: qsTr("This device"); deviceKind: "controller"; deviceReach: "online"
                selected: win.selected === "taos-agent"
                onClicked: win.selected = "taos-agent"
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                // skeleton while the first answer is on its way
                Column {
                    objectName: "skeleton"
                    visible: !dir.loaded && dir.errorText === ""
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                    spacing: 18
                    Accessible.role: Accessible.StaticText
                    Accessible.name: qsTr("Loading agents")
                    Repeater {
                        model: 4
                        delegate: Row {
                            spacing: 12
                            Rectangle { width: 36; height: 36; radius: 18; color: Look.raised; opacity: pulse.v }
                            Column {
                                spacing: 8
                                Rectangle { width: 160; height: 10; radius: 5; color: Look.raised; opacity: pulse.v }
                                Rectangle { width: 100; height: 8; radius: 4; color: Look.raised; opacity: pulse.v }
                            }
                        }
                    }
                    QtObject {
                        id: pulse
                        property real v: 0.6
                        SequentialAnimation on v {
                            running: !Look.reduceMotion && !dir.loaded
                            loops: Animation.Infinite
                            NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 0.5; duration: 700; easing.type: Easing.InOutSine }
                        }
                    }
                }

                ListView {
                    id: list
                    objectName: "agentList"
                    anchors.fill: parent
                    visible: dir.loaded && count > 0
                    clip: true
                    model: dir.model
                    currentIndex: -1
                    keyNavigationEnabled: true
                    boundsBehavior: Flickable.StopAtBounds
                    activeFocusOnTab: true
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("Agents")
                    C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }
                    section.property: dir.groupByDevice ? "deviceName" : ""
                    section.criteria: ViewSection.FullString
                    section.delegate: Item {
                        required property string section
                        width: list.width; height: 30
                        Accessible.role: Accessible.StaticText
                        Accessible.name: qsTr("Device %1").arg(section)
                        TaosText { x: 16; anchors.verticalCenter: parent.verticalCenter; text: section
                                   font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.textTertiary
                                   font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6 }
                    }
                    add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Look.base } }
                    displaced: Transition { NumberAnimation { properties: "y"; duration: Look.base; easing.type: Easing.OutCubic } }
                    delegate: Item {
                        id: del
                        required property int index
                        required property string name
                        required property string title
                        required property string agentState
                        required property string chatModel
                        required property string framework
                        required property string busy
                        required property string actionError
                        required property string ownerLabel
                        required property string deviceName
                        required property string deviceKind
                        required property string deviceReach
                        width: list.width
                        height: 74
                        AgentRow {
                            anchors.fill: parent
                            agentName: del.name
                            title: del.title
                            agentState: del.agentState
                            subtitle: [del.framework, del.chatModel].filter(s => s !== "").join(" · ")
                            busyLabel: del.busy !== "" ? dir.progressLabel(del.busy) : ""
                            errorText: del.actionError
                            ownerLabel: dir.isAdmin ? del.ownerLabel : ""
                            deviceName: del.deviceName
                            deviceKind: del.deviceKind
                            deviceReach: del.deviceReach
                            selected: win.selected === del.name
                            onClicked: { win.selected = del.name; list.currentIndex = del.index }
                            Keys.onReturnPressed: win.selected = del.name
                        }
                    }
                }

                EmptyState {
                    objectName: "noMatches"
                    anchors.centerIn: parent
                    visible: dir.loaded && dir.totalCount > 0 && list.count === 0
                    glyph: "filter"
                    title: qsTr("No agents match")
                    detail: qsTr("Change the search or the filters.")
                }
                ColumnLayout {
                    objectName: "emptyState"
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 48, 340)
                    visible: dir.loaded && dir.totalCount === 0 && dir.errorText === ""
                    spacing: 12
                    EmptyState {
                        Layout.fillWidth: true
                        glyph: "inbox"
                        title: qsTr("No agents of your own yet")
                        detail: qsTr("An agent is an AI worker that runs in its own container, on this device or another one in your cluster. Deploy one from the web app. Deploying from this window is coming.")
                    }
                    TaosButton {
                        objectName: "openWizard"
                        Layout.alignment: Qt.AlignHCenter
                        text: qsTr("Open the web app to deploy"); variant: "primary"
                        accessibleName: qsTr("Open the web app to deploy an agent")
                        onClicked: dir.openWebWizard()
                    }
                }
                ColumnLayout {
                    objectName: "errorState"
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 48, 340)
                    visible: dir.errorText !== "" && !dir.loaded
                    spacing: 12
                    EmptyState {
                        Layout.fillWidth: true
                        glyph: dir.errorKind === "forbidden" ? "shield" : "alert"
                        tone: dir.errorKind === "forbidden" ? "warning" : "danger"
                        title: dir.errorKind === "forbidden" ? qsTr("You do not have access to agents")
                             : dir.errorKind === "network" ? qsTr("Can't reach the controller") : qsTr("Could not load agents")
                        detail: dir.errorText
                    }
                    TaosButton { Layout.alignment: Qt.AlignHCenter; text: qsTr("Try again"); onClicked: dir.refresh() }
                }
            }
            // a failed refresh keeps the last list and says so
            Rectangle {
                objectName: "staleBar"
                Layout.fillWidth: true
                visible: dir.loaded && dir.errorText !== ""
                implicitHeight: 30
                color: Look.surface
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Look.hairline }
                TaosText { anchors.centerIn: parent; text: qsTr("Could not refresh: %1").arg(dir.errorText)
                           color: Look.warning; font.pixelSize: Look.small; elide: Text.ElideRight; width: parent.width - 24
                           horizontalAlignment: Text.AlignHCenter
                           Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text }
            }
        }

        Rectangle { Layout.fillHeight: true; width: 1; color: Look.hairline; visible: !win.narrow }

        // ---- detail ---------------------------------------------------------------
        Item {
            Layout.fillHeight: true
            Layout.fillWidth: true
            visible: !win.narrow || win.selected !== ""
            DetailPane {
                objectName: "detail"
                anchors.fill: parent
                visible: win.selected !== ""
                dir: dir
                agentName: win.selected
                narrow: win.narrow
                onBack: win.selected = ""
            }
            EmptyState {
                anchors.centerIn: parent
                visible: win.selected === "" && !win.narrow && dir.errorText === ""
                glyph: "info"
                title: qsTr("Select an agent")
                detail: qsTr("Its state, where it runs and its controls appear here.")
            }
        }
    }

    // transient message (a launch that failed, an unknown --agent)
    Rectangle {
        objectName: "toast"
        visible: win.toastText !== ""
        anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter; bottomMargin: 20 }
        width: Math.min(parent.width - 32, toastLabel.implicitWidth + 32); height: 36; radius: 18
        color: Look.raised; border.width: 1; border.color: Look.hairline
        Accessible.role: Accessible.AlertMessage; Accessible.name: win.toastText
        TaosText { id: toastLabel; anchors.centerIn: parent; text: win.toastText; font.pixelSize: Look.body
                   elide: Text.ElideRight; width: parent.width - 32 }
    }
}
