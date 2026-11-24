// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// The one action of an app, as its state says: Install, a progress, Open, Retry. `engine` is the StoreEngine and
// `entry` the app's row; it reads the install state from the engine so a list and the detail page agree.
RowLayout {
    id: root
    property var engine
    property var entry: ({})
    property bool compact: true
    property bool showRemove: false
    property string pick: ""
    signal review()
    readonly property var st: engine && entry.id ? (engine.revision >= 0 ? engine.installState(entry.id) : ({})) : ({})
    readonly property string state: st.state ? st.state : "idle"
    readonly property bool inst: entry.installed === true
    readonly property string kind: entry.kind ? entry.kind : ""
    readonly property bool idleOrDone: state === "idle" || state === "installed"
    spacing: 8

    // progress while installing
    ColumnLayout {
        visible: root.state === "installing" || root.state === "removing"
        spacing: 3
        Layout.preferredWidth: 140
        TaosText {
            text: (root.st.text ? root.st.text : "") + (root.st.percent >= 0 ? "  " + Math.round(root.st.percent) + "%" : "")
            font.pixelSize: Look.small
            color: Look.textSecondary
            font.features: { "tnum": 1 }
            Accessible.ignored: false
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
        Rectangle {
            Layout.fillWidth: true; height: 3; radius: 1.5
            color: Look.field
            Rectangle {
                id: bar
                height: parent.height; radius: 1.5
                color: Look.accent
                width: root.st.percent >= 0 ? parent.width * Math.min(100, root.st.percent) / 100 : parent.width * 0.3
                Behavior on width { NumberAnimation { duration: Look.base; easing.type: Easing.OutCubic } }
                SequentialAnimation on x {
                    running: root.st.percent < 0 && !Look.reduceMotion && (root.state === "installing" || root.state === "removing")
                    loops: Animation.Infinite
                    NumberAnimation { from: 0; to: bar.parent.width * 0.7; duration: 900; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: bar.parent.width * 0.7; to: 0; duration: 900; easing.type: Easing.InOutQuad }
                }
            }
        }
    }

    TaosButton {
        visible: root.state === "consent"
        text: qsTr("Review")
        variant: "primary"
        compact: root.compact
        accessibleName: qsTr("Review what %1 needs before installing").arg(root.entry.name ? root.entry.name : "")
        onClicked: root.review()
    }
    TaosButton {
        visible: root.state === "failed"
        text: qsTr("Retry")
        variant: "default"
        compact: root.compact
        accessibleName: qsTr("Retry installing %1").arg(root.entry.name)
        onClicked: { root.engine.dismiss(root.entry.id); root.engine.install(root.entry.id, root.pick) }
    }
    // not installed
    TaosButton {
        visible: root.idleOrDone && !root.inst && root.kind !== "webapp" && root.kind !== ""
        text: qsTr("Install")
        variant: "primary"
        compact: root.compact
        accessibleName: qsTr("Install %1").arg(root.entry.name)
        onClicked: root.engine.install(root.entry.id, root.pick)
    }
    // web apps: Add, and Add another once there is one
    TaosButton {
        visible: root.kind === "webapp"
        text: root.inst ? qsTr("Add another") : qsTr("Add")
        variant: root.inst ? "default" : "primary"
        compact: root.compact
        accessibleName: root.inst ? qsTr("Add another copy of %1 with its own sign-in").arg(root.entry.name) : qsTr("Add %1 as a web app").arg(root.entry.name)
        enabled: root.engine ? !root.engine.webapps.adding() : false
        onClicked: root.engine.installWebapp(root.entry.id)
    }
    // installed
    TaosButton {
        visible: root.idleOrDone && root.inst && root.kind !== "webapp" && root.kind !== "model" && root.kind !== "plugin" && root.kind !== "mcp" && root.kind !== ""
        text: qsTr("Open")
        variant: "primary"
        compact: root.compact
        accessibleName: qsTr("Open %1").arg(root.entry.name)
        onClicked: root.engine.open(root.entry.id)
    }
    Chip {
        visible: root.idleOrDone && root.inst && (root.kind === "model" || root.kind === "plugin" || root.kind === "mcp")
        text: qsTr("Installed"); tone: "success"; glyph: "check"
    }
    TaosButton {
        visible: root.showRemove && root.idleOrDone && root.inst && root.kind !== "webapp" && root.kind !== ""
        text: qsTr("Remove")
        variant: "danger"
        compact: root.compact
        accessibleName: qsTr("Remove %1").arg(root.entry.name)
        onClicked: root.engine.remove(root.entry.id)
    }
}
