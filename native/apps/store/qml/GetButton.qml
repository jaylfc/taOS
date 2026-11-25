// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// The one action of an app as its state says it: Get, a progress, Open, Retry. `engine` is the StoreEngine and
// `entry` the app's row; the install state is read from the engine so a card, a row and the detail page agree.
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
    readonly property string nm: entry.name ? entry.name : ""
    spacing: Look.space2

    // progress while installing or removing
    PillButton {
        visible: root.state === "installing" || root.state === "removing" || root.state === "moving"
        compact: root.compact
        variant: "outline"
        enabled: false
        text: root.st.percent >= 0 ? Math.round(root.st.percent) + "%" : (root.st.text ? root.st.text : qsTr("Working"))
        accessibleName: (root.st.text ? root.st.text : qsTr("Working")) + (root.st.percent >= 0 ? ", " + Math.round(root.st.percent) + "%" : "")
    }
    PillButton {
        visible: root.state === "consent"
        text: qsTr("Review"); variant: "primary"; compact: root.compact
        accessibleName: qsTr("Review what %1 needs before installing").arg(root.nm)
        onClicked: root.review()
    }
    PillButton {
        visible: root.state === "failed" && root.st.op !== "move" && root.st.op !== "update"
        text: qsTr("Retry"); compact: root.compact
        accessibleName: qsTr("Retry installing %1").arg(root.nm)
        onClicked: { root.engine.dismiss(root.entry.id); root.engine.install(root.entry.id, root.pick) }
    }
    // not installed
    PillButton {
        visible: root.idleOrDone && !root.inst && root.kind !== "webapp" && root.kind !== ""
        text: qsTr("Get"); compact: root.compact
        variant: root.compact ? "tint" : "primary"
        accessibleName: qsTr("Install %1").arg(root.nm)
        onClicked: root.engine.install(root.entry.id, root.pick)
    }
    // web apps: Get adds one, Add another adds a second with its own sign-in
    PillButton {
        visible: root.kind === "webapp"
        text: root.inst ? qsTr("Add another") : qsTr("Get"); compact: root.compact
        variant: root.inst || root.compact ? "tint" : "primary"
        accessibleName: root.inst ? qsTr("Add another copy of %1 with its own sign-in").arg(root.nm) : qsTr("Add %1 as a web app").arg(root.nm)
        enabled: root.engine ? !root.engine.webapps.adding() : false
        onClicked: root.engine.installWebapp(root.entry.id)
    }
    // installed
    PillButton {
        visible: root.idleOrDone && root.inst && root.kind !== "webapp" && root.kind !== "model" && root.kind !== "plugin" && root.kind !== "mcp" && root.kind !== ""
        text: qsTr("Open"); compact: root.compact
        variant: root.compact ? "tint" : "primary"
        accessibleName: qsTr("Open %1").arg(root.nm)
        onClicked: root.engine.open(root.entry.id)
    }
    PillButton {
        visible: root.idleOrDone && root.inst && (root.kind === "model" || root.kind === "plugin" || root.kind === "mcp")
        text: qsTr("Installed"); glyph: "check"; compact: root.compact; variant: "outline"; enabled: false
        accessibleName: qsTr("%1 is installed").arg(root.nm)
    }
    PillButton {
        visible: root.showRemove && root.idleOrDone && root.inst && root.kind !== "webapp" && root.kind !== ""
        text: qsTr("Remove"); compact: root.compact; variant: "outline"
        accessibleName: qsTr("Remove %1").arg(root.nm)
        onClicked: root.engine.remove(root.entry.id)
    }
}
