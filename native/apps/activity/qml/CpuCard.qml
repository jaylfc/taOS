import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var cpu: eng.activity.cpu || ({})
    readonly property var cores: cpu.cores || []
    readonly property var avg: cpu.load_avg || null
    title: qsTr("CPU")
    summary: qsTr("%1% overall").arg(Math.round(cpu.overall_percent || 0))
             + (avg ? " · " + avg.map(function (v) { return v.toFixed(2) }).join(" ") : "")
    Hero {
        big: Math.round(c.cpu.overall_percent || 0) + "%"
        caption: Fmt.hardwareLabel("cpu", c.eng.hardware)
        graph.values: c.eng.history.cpu || []
        graph.title: qsTr("CPU load")
        graph.color: Pal.series1
    }
    GridLayout {
        Layout.fillWidth: true
        columns: c.width > 560 ? 2 : 1
        columnSpacing: 20
        rowSpacing: 6
        Repeater {
            model: c.cores
            LoadBar {
                required property var modelData
                Layout.fillWidth: true
                label: Fmt.coreLabel(modelData)
                value: modelData.load_percent || 0
            }
        }
    }
}
