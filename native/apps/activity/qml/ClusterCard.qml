import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var workers: eng.workers
    title: qsTr("Cluster (%1 worker%2)").arg(workers.length).arg(workers.length === 1 ? "" : "s")
    dot: Pal.ok
    function stateTone(s) { return s === 0 ? Pal.ok : s === 1 ? Pal.warn : s === 2 ? Pal.crit : TaosStyle.textTertiary }
    function hardware(w) {
        var hw = w.hardware || {}
        var parts = []
        var cpu = hw.cpu && hw.cpu.model ? hw.cpu.model.split("@")[0].trim() : ""
        parts.push(cpu || qsTr("Unknown CPU"))
        parts.push(hw.ram_mb ? Math.round(hw.ram_mb / 1024) + "GB RAM" : "? RAM")
        var g = Fmt.hardwareLabel("gpu", hw), n = Fmt.hardwareLabel("npu", hw)
        if (g) parts.push(g)
        if (n) parts.push(n)
        return parts.join(" · ")
    }
    trailing: GlassButton {
        text: "↻"
        implicitWidth: 32
        padding: 0; leftPadding: 0; rightPadding: 0
        Accessible.name: qsTr("Refresh cluster workers")
        onClicked: c.eng.refreshWorkers()
    }
    ColumnLayout {
        visible: c.workers.length === 0
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignHCenter
        spacing: 10
        Txt { text: qsTr("No workers registered yet."); font.pixelSize: 12; color: TaosStyle.textTertiary; Layout.alignment: Qt.AlignHCenter }
        GlassButton {
            text: qsTr("How to add a worker")
            Layout.alignment: Qt.AlignHCenter
            Accessible.name: qsTr("How to add a worker (opens docs in your browser)")
            onClicked: Qt.openUrlExternally("https://github.com/jaylfc/tinyagentos#distributed-compute-cluster")
        }
    }
    GridLayout {
        visible: c.workers.length > 0
        Layout.fillWidth: true
        columns: c.width > 640 ? 2 : 1
        columnSpacing: 10
        rowSpacing: 10
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Cluster workers")
        Repeater {
            model: c.workers
            Rectangle {
                id: tile
                required property var modelData
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                implicitHeight: tl.implicitHeight + 20
                radius: Pal.innerRadius
                color: Pal.inner
                border.width: 1
                border.color: Pal.hairline
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name
                Accessible.description: qsTr("Status: %1").arg(modelData.stateLabel)
                ColumnLayout {
                    id: tl
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
                    spacing: 6
                    RowLayout {
                        spacing: 8
                        Txt { text: tile.modelData.name; font.weight: Font.DemiBold }
                        Txt {
                            visible: text.length > 0
                            text: tile.modelData.shortHost ? "· " + tile.modelData.shortHost : ""
                            font.pixelSize: 11; color: TaosStyle.textTertiary; Layout.fillWidth: true
                        }
                        Item { Layout.fillWidth: !tile.modelData.shortHost }
                        Chip {
                            visible: !!tile.modelData.tier_id
                            text: tile.modelData.tier_id || ""
                            mono: true; outlined: true
                            Accessible.name: qsTr("Hardware tier: %1").arg(tile.modelData.tier_id)
                        }
                        Chip {
                            text: tile.modelData.stateLabel
                            tone: c.stateTone(tile.modelData.state)
                            Accessible.name: qsTr("Status: %1").arg(tile.modelData.stateLabel)
                        }
                    }
                    Txt {
                        visible: !!tile.modelData.hardware
                        text: c.hardware(tile.modelData)
                        font.pixelSize: 11; color: TaosStyle.textTertiary; Layout.fillWidth: true
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 5
                        Txt {
                            visible: tile.modelData.backends.length === 0
                            text: qsTr("No backends loaded"); font.pixelSize: 11; font.italic: true; color: TaosStyle.textTertiary
                        }
                        Repeater {
                            model: tile.modelData.backends
                            Chip { required property var modelData; text: modelData.display; tone: Pal.info }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 5
                        Txt {
                            visible: (tile.modelData.capabilities || []).length === 0 && tile.modelData.latent.length === 0
                            text: qsTr("No capabilities yet"); font.pixelSize: 11; font.italic: true; color: TaosStyle.textTertiary
                        }
                        Repeater {
                            model: tile.modelData.capabilities || []
                            Chip {
                                required property var modelData
                                text: modelData; tone: Pal.ok
                                Accessible.name: qsTr("Current capability: %1").arg(modelData)
                            }
                        }
                        Repeater {
                            model: tile.modelData.latent
                            Chip {
                                required property var modelData
                                text: modelData; outlined: true
                                Accessible.name: qsTr("Potential capability: %1").arg(modelData)
                            }
                        }
                    }
                }
            }
        }
    }
}
