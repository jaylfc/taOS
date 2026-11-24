import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The detail of one agent: who it is, where it runs, its state, lifecycle
// controls and what the controller reports. Every control can be refused by the
// controller (403); the refusal text sits next to the controls, in words.
Item {
    id: pane
    property var dir: null
    property string agentName: ""
    property bool narrow: false
    signal back()
    signal toast(string text)

    readonly property bool isTaos: agentName === "taos-agent"
    // re-evaluated when the rows change
    readonly property var a: { dir.revision; return isTaos ? dir.taosAgent : dir.agent(agentName) }
    readonly property bool present: isTaos ? true : (a && a.name !== undefined)
    readonly property string title: isTaos ? qsTr("taOS Agent") : (a.display_name ? a.display_name : agentName)
    readonly property string agentState: isTaos ? "running" : (a.state ? a.state : "")
    readonly property var device: (a && a.device) ? a.device : ({})
    readonly property string busy: { dir.revision; return dir.busyAction(agentName) }
    readonly property string failure: { dir.revision; return dir.actionError(agentName) }
    property string confirming: ""

    onAgentNameChanged: { confirming = ""; if (agentName !== "" && !isTaos) dir.loadActivity(agentName); else dir.clearActivity() }
    Connections { target: dir; function onActionFinished(name, action, ok, text) { if (name === pane.agentName) pane.confirming = "" } }

    function run(action) {
        if (Tone.needsConfirm(action) && confirming !== action) { confirming = action; return }
        confirming = ""
        dir.act(agentName, action)
    }

    Flickable {
        id: flick
        anchors.fill: parent
        visible: pane.present
        contentWidth: width
        contentHeight: col.implicitHeight + 40
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }
        ColumnLayout {
            id: col
            x: 24; y: 20
            width: flick.width - 48
            spacing: 16

            // ---- header ------------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                TaosButton {
                    visible: pane.narrow
                    glyph: ""; text: qsTr("Back"); compact: true; variant: "ghost"
                    accessibleName: qsTr("Back to agents")
                    onClicked: pane.back()
                }
                Rectangle {
                    width: 48; height: 48; radius: 24; color: Look.raised
                    border.width: 1; border.color: Look.hairline
                    TaosText { anchors.centerIn: parent; text: Tone.initial(pane.title); font.pixelSize: 20
                               font.weight: Font.DemiBold; color: Look.textSecondary }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    TaosText {
                        Layout.fillWidth: true; text: pane.title; elide: Text.ElideRight
                        font.pixelSize: Look.heading; font.weight: Font.DemiBold
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    RowLayout {
                        spacing: 10
                        TaosText {
                            objectName: "detailState"
                            text: pane.busy !== "" ? dir.progressLabel(pane.busy) + "..." : Tone.stateLabel(pane.agentState)
                            font.pixelSize: Look.body; font.weight: Font.DemiBold
                            color: pane.busy !== "" ? Look.textSecondary : Tone.stateColor(pane.agentState)
                            Accessible.ignored: false; Accessible.role: Accessible.StaticText
                            Accessible.name: qsTr("State: %1").arg(text)
                        }
                        DeviceTag {
                            visible: !pane.isTaos
                            deviceName: pane.device.local ? qsTr("This device") : (pane.device.name || "")
                            kind: pane.device.kind || ""; reach: pane.device.reach || ""
                        }
                    }
                }
                TaosButton {
                    objectName: "messageButton"
                    visible: !pane.isTaos
                    text: qsTr("Message"); glyph: ""; variant: "primary"
                    accessibleName: qsTr("Message %1").arg(pane.title)
                    enabled: dir.canMessage
                    onClicked: dir.messageAgent(pane.agentName)
                    C.ToolTip.visible: hovered && !dir.canMessage
                    C.ToolTip.text: qsTr("The Messages app is not installed on this device.")
                }
            }

            // ---- lifecycle ---------------------------------------------------
            RowLayout {
                visible: !pane.isTaos
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: (pane.a && pane.a.actions) ? pane.a.actions : []
                    delegate: TaosButton {
                        required property string modelData
                        objectName: "action_" + modelData
                        text: Tone.actionLabel(modelData)
                        variant: modelData === "stop" ? "danger" : "default"
                        enabled: pane.busy === ""
                        accessibleName: qsTr("%1 %2").arg(Tone.actionLabel(modelData)).arg(pane.title)
                        onClicked: pane.run(modelData)
                    }
                }
                TaosText {
                    visible: !pane.a.actions || pane.a.actions.length === 0
                    text: pane.agentState === "deploying" ? qsTr("Deploying. Controls return when it is done.") : ""
                    color: Look.textSecondary; font.pixelSize: Look.small
                }
                Item { Layout.fillWidth: true }
            }

            // confirmation, inline so the keyboard stays where it was
            RowLayout {
                objectName: "confirmBar"
                visible: pane.confirming !== ""
                Layout.fillWidth: true
                spacing: 10
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Confirm %1").arg(Tone.actionLabel(pane.confirming))
                TaosText {
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    text: pane.confirming === "stop"
                        ? qsTr("Stop %1? Its container shuts down and it stops answering until you start it again.").arg(pane.title)
                        : qsTr("Restart %1? Work in progress may be interrupted.").arg(pane.title)
                    color: Look.text; font.pixelSize: Look.body
                }
                TaosButton { objectName: "confirmYes"; text: Tone.actionLabel(pane.confirming)
                             variant: pane.confirming === "stop" ? "danger" : "primary"
                             accessibleName: qsTr("Confirm %1").arg(Tone.actionLabel(pane.confirming))
                             onClicked: pane.run(pane.confirming) }
                TaosButton { objectName: "confirmNo"; text: qsTr("Cancel"); variant: "ghost"; onClicked: pane.confirming = "" }
            }

            // refusal or failure
            RowLayout {
                objectName: "failureBar"
                visible: pane.failure !== ""
                Layout.fillWidth: true
                spacing: 10
                Accessible.role: Accessible.AlertMessage
                Accessible.name: pane.failure
                AgentIcon { name: pane.failure.indexOf("not allowed") >= 0 ? "lock" : "alert"; size: 16; color: Look.danger
                            Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                TaosText { Layout.fillWidth: true; text: pane.failure; wrapMode: Text.Wrap; color: Look.text; font.pixelSize: Look.body }
                TaosButton { text: qsTr("Dismiss"); compact: true; variant: "ghost"; onClicked: dir.clearActionError(pane.agentName) }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }

            // ---- overview ----------------------------------------------------
            TaosText { text: qsTr("Overview"); font.pixelSize: Look.title; font.weight: Font.DemiBold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            GridLayout {
                id: facts
                objectName: "facts"
                Layout.fillWidth: true
                columns: 2; columnSpacing: 24; rowSpacing: 10
                function row(label, value) { return { label: label, value: value } }
                readonly property var items: {
                    const a = pane.a || ({}), d = pane.device, c = a.container || null
                    const out = []
                    if (pane.isTaos) {
                        out.push(row(qsTr("Model"), a.model || qsTr("Not chosen")))
                        out.push(row(qsTr("Framework"), a.framework || qsTr("Not reported")))
                        out.push(row(qsTr("Permitted models"), (a.permitted_models && a.permitted_models.length) ? a.permitted_models.join(", ") : qsTr("None listed")))
                        out.push(row(qsTr("Runs on"), qsTr("This device, built in. It cannot be stopped or deleted.")))
                        return out
                    }
                    out.push(row(qsTr("Device"), (d.local ? qsTr("This device, the controller") : (d.name || qsTr("Unknown")) + ", " + Tone.deviceKindLabel(d.kind || "")) + ", " + Tone.reachLabel(d.reach || "")))
                    out.push(row(qsTr("Framework"), a.framework || qsTr("Not set")))
                    out.push(row(qsTr("Model"), a.model || qsTr("Not set")))
                    out.push(row(qsTr("Address"), a.host || qsTr("Not reported")))
                    out.push(row(qsTr("Container"), c ? (c.name + ", " + c.status + (c.ip ? ", " + c.ip : "") + (c.memory_mb ? ", " + Math.round(c.memory_mb) + " MB" : "") + (c.cpu_cores ? ", " + c.cpu_cores + " cores" : "")) : qsTr("No container reported")))
                    out.push(row(qsTr("Memory vectors"), Number(a.vectors || 0).toLocaleString(Qt.locale(), "f", 0)))
                    if (dir.isAdmin) out.push(row(qsTr("Owner"), a.owner_id || qsTr("Not attributed")))
                    out.push(row(qsTr("Uptime"), qsTr("Not reported by the controller")))
                    return out
                }
                Repeater {
                    model: facts.items
                    delegate: Item {
                        required property var modelData
                        required property int index
                        Layout.columnSpan: 2
                        Layout.fillWidth: true
                        implicitHeight: line.implicitHeight
                        Accessible.role: Accessible.StaticText
                        Accessible.name: modelData.label + ": " + modelData.value
                        RowLayout {
                            id: line
                            anchors.left: parent.left; anchors.right: parent.right
                            spacing: 24
                            TaosText { Layout.preferredWidth: 130; Layout.alignment: Qt.AlignTop
                                       text: modelData.label; color: Look.textSecondary; font.pixelSize: Look.body }
                            TaosText { Layout.fillWidth: true; text: modelData.value; wrapMode: Text.Wrap
                                       font.pixelSize: Look.body; font.features: { "tnum": 1 } }
                        }
                    }
                }
            }

            // ---- recent activity ----------------------------------------------
            TaosText { visible: !pane.isTaos; text: qsTr("Recent activity"); font.pixelSize: Look.title; font.weight: Font.DemiBold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText {
                visible: !pane.isTaos && dir.activityState !== "ready"
                text: dir.activityState === "loading" ? qsTr("Loading...")
                    : dir.activityState === "forbidden" ? qsTr("You are not allowed to see this agent's activity.")
                    : dir.activityState === "error" ? qsTr("Could not load the activity.")
                    : qsTr("No activity recorded yet.")
                color: Look.textSecondary; font.pixelSize: Look.body
            }
            Repeater {
                model: pane.isTaos ? [] : dir.activity
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 12
                    Accessible.role: Accessible.StaticText
                    Accessible.name: modelData.kind + ", " + Format.relativeTime(Format.toMillis(modelData.at), Format.tick)
                    TaosText { Layout.preferredWidth: 130; text: Format.relativeTime(Format.toMillis(modelData.at), Format.tick)
                               color: Look.textSecondary; font.pixelSize: Look.small; font.features: { "tnum": 1 } }
                    TaosText { Layout.fillWidth: true; elide: Text.ElideRight
                               text: modelData.kind + (modelData.model ? " (" + modelData.model + ")" : "") + (modelData.error ? ", " + modelData.error : "")
                               color: modelData.error ? Look.danger : Look.text; font.pixelSize: Look.body }
                }
            }
            TaosText {
                visible: !pane.isTaos
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: qsTr("Model and permissions, skills, logs, budget and persona are not in this version of the native app yet. They are in the web app.")
                color: Look.textTertiary; font.pixelSize: Look.small
            }
        }
    }

    EmptyState {
        anchors.centerIn: parent
        visible: !pane.present && pane.agentName !== ""
        glyph: "info"
        title: qsTr("This agent is not in your list")
        detail: qsTr("It may have been archived, or it is not yours to see.")
    }
}
