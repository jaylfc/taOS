pragma Singleton
import QtQuick
import Taos.Apps 1.0

// Words and semantic tokens for agent state and device reachability. Colour never
// carries the meaning alone: every state has its own word.
QtObject {
    function stateLabel(s) {
        switch (s) {
        case "running": return qsTr("Running")
        case "paused": return qsTr("Paused")
        case "stopped": return qsTr("Stopped")
        case "error": return qsTr("Error")
        case "deploying": return qsTr("Deploying")
        default: return qsTr("Unknown")
        }
    }
    function stateColor(s) {
        switch (s) {
        case "running": return Look.success
        case "paused": return Look.warning
        case "error": return Look.danger
        default: return Look.textSecondary
        }
    }
    function reachLabel(r) {
        return r === "online" ? qsTr("Online") : r === "offline" ? qsTr("Offline") : qsTr("Status unknown")
    }
    function reachColor(r) {
        return r === "offline" ? Look.danger : r === "online" ? Look.textSecondary : Look.textTertiary
    }
    function deviceIcon(kind) {
        switch (kind) {
        case "phone": case "mobile": return "phone"
        case "controller": return "monitor"
        default: return "server"
        }
    }
    function deviceKindLabel(kind) {
        switch (kind) {
        case "phone": case "mobile": return qsTr("Phone")
        case "controller": return qsTr("This device")
        case "": return qsTr("Device")
        default: return kind.charAt(0).toUpperCase() + kind.slice(1)
        }
    }
    function needsConfirm(a) { return a === "stop" || a === "restart" }
    function initial(title) { return title.length > 0 ? title.charAt(0).toUpperCase() : "?" }
    function actionLabel(a) {
        switch (a) {
        case "start": return qsTr("Start")
        case "stop": return qsTr("Stop")
        case "restart": return qsTr("Restart")
        case "pause": return qsTr("Pause")
        case "resume": return qsTr("Resume")
        default: return a
        }
    }
    function actionGlyph(a) {
        switch (a) {
        case "start": case "resume": return "play"
        case "stop": return "stop"
        case "restart": return "restart"
        default: return "pause"
        }
    }
}
