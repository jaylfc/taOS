import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var st: eng.scheduler
    readonly property var resources: st.resources || []
    readonly property var tiers: ["GPU", "NPU", "CPU", "CLUSTER"]
    // The controller sends the tier as 0..3; accept the names too.
    function tierIndex(t) { return typeof t === "number" ? t : tiers.indexOf(String(t).toUpperCase()) }
    function tierTone(i) { return [Pal.ok, Pal.info, Pal.violet, Pal.warn][tierIndex(i)] || TaosStyle.textTertiary }
    function resourceLabel(r) {
        var kind = ["gpu", "npu", "cpu"][tierIndex(r.tier)]
        var hw = kind ? Fmt.hardwareLabel(kind, eng.hardware) : ""
        return hw || r.name
    }
    function taskTone(s) {
        return s === "complete" ? Pal.ok : s === "running" ? Pal.info : s === "error" ? Pal.crit : s === "rejected" ? Pal.warn : TaosStyle.textTertiary
    }
    title: qsTr("Scheduler")
    dot: Pal.info
    summary: qsTr("%1 active").arg(st.active || 0)

    RowLayout {
        Layout.fillWidth: true
        spacing: 22
        Repeater {
            model: [
                { k: qsTr("submitted"), v: c.st.submitted || 0, t: 0 },
                { k: qsTr("completed"), v: c.st.completed || 0, t: 0 },
                { k: qsTr("active"), v: c.st.active || 0, t: 1 },
                { k: qsTr("rejected"), v: c.st.rejected || 0, t: 2 },
                { k: qsTr("errors"), v: c.st.errors || 0, t: 3 }
            ]
            ColumnLayout {
                required property var modelData
                spacing: 0
                Accessible.role: Accessible.StaticText
                Accessible.name: modelData.v + " " + modelData.k
                Txt {
                    text: parent.modelData.v
                    font.pixelSize: 20; font.weight: Font.DemiBold
                    color: parent.modelData.t === 3 && parent.modelData.v > 0 ? Pal.crit
                         : parent.modelData.t === 2 && parent.modelData.v > 0 ? Pal.warn
                         : parent.modelData.t === 1 ? Pal.ok : TaosStyle.text
                }
                Txt { text: parent.modelData.k; font.pixelSize: 11; color: TaosStyle.textTertiary }
            }
        }
        Item { Layout.fillWidth: true }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: c.width > 640 ? 2 : 1
        columnSpacing: 10
        rowSpacing: 10
        Repeater {
            model: c.resources
            Rectangle {
                id: rt
                required property var modelData
                readonly property var caps: modelData.capabilities || []
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                implicitHeight: rl.implicitHeight + 20
                radius: Pal.innerRadius
                color: Pal.inner
                border.width: 1
                border.color: Pal.hairline
                ColumnLayout {
                    id: rl
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
                    spacing: 5
                    RowLayout {
                        spacing: 8
                        Chip { text: c.tiers[c.tierIndex(rt.modelData.tier)] || "?"; tone: c.tierTone(rt.modelData.tier) }
                        Txt { text: c.resourceLabel(rt.modelData); font.weight: Font.Medium; Layout.fillWidth: true }
                        Txt {
                            text: qsTr("%1 active · %2 slots").arg(rt.modelData.in_flight).arg(rt.modelData.concurrency)
                            font.pixelSize: 11; color: TaosStyle.textTertiary
                        }
                    }
                    Txt {
                        Layout.fillWidth: true
                        font.pixelSize: 11; color: TaosStyle.textTertiary
                        text: qsTr("controller") + " · " + (rt.modelData.platform || "") + " · " + (rt.modelData.runtime || "")
                              + (rt.modelData.runtime_version ? " " + rt.modelData.runtime_version : "")
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 5
                        Repeater { model: rt.caps; Chip { required property var modelData; text: modelData; tone: Pal.ok } }
                        Repeater {
                            model: (rt.modelData.potential_capabilities || []).filter(function (x) { return rt.caps.indexOf(x) < 0 })
                            Chip { required property var modelData; text: modelData; outlined: true }
                        }
                    }
                }
            }
        }
    }
    ColumnLayout {
        visible: c.eng.tasks.length > 0
        Layout.fillWidth: true
        spacing: 2
        Txt { text: qsTr("Recent tasks"); font.pixelSize: 11; color: TaosStyle.textTertiary }
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Recent tasks")
        Repeater {
            model: c.eng.tasks
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: 10
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.status + ", " + modelData.capability
                Txt { text: modelData.status; font.pixelSize: 11; color: c.taskTone(modelData.status); Layout.preferredWidth: 64 }
                Txt { text: modelData.capability; font.pixelSize: 11; color: TaosStyle.textSecondary; Layout.preferredWidth: 110 }
                Txt { text: modelData.resource || "-"; font.pixelSize: 11; Layout.preferredWidth: 110 }
                Txt { text: modelData.submitter || ""; font.pixelSize: 11; color: TaosStyle.textTertiary; Layout.fillWidth: true }
                Txt {
                    text: modelData.elapsed_seconds !== null && modelData.elapsed_seconds !== undefined ? Number(modelData.elapsed_seconds).toFixed(1) + "s" : ""
                    font.pixelSize: 11; color: TaosStyle.textTertiary
                    horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 48
                }
            }
        }
    }
}
