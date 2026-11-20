import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var ifaces: eng.activity.network || []
    title: qsTr("Network")
    dot: Pal.info
    summary: ifaces.length === 1 ? ifaces[0].name : qsTr("%1 interfaces").arg(ifaces.length)
    Sparkline {
        Layout.fillWidth: true
        Layout.preferredHeight: 72
        values: c.eng.history.netRx || []
        values2: c.eng.history.netTx || []
        title: qsTr("Network download"); title2: qsTr("Network upload")
        color: Pal.info; color2: Pal.violet
        maxValue: 0
        format: function (v) { return Fmt.rate(v) }
    }
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 5
        Repeater {
            model: c.ifaces
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("%1: download %2, upload %3").arg(modelData.name).arg(Fmt.rate(modelData.rx_bps)).arg(Fmt.rate(modelData.tx_bps))
                Txt { text: modelData.name; font.pixelSize: 12; color: TaosStyle.textSecondary; Layout.fillWidth: true }
                Txt { text: "↓ " + Fmt.rate(modelData.rx_bps); font.pixelSize: 12; color: Pal.info }
                Txt { text: "↑ " + Fmt.rate(modelData.tx_bps); font.pixelSize: 12; color: Pal.violet }
            }
        }
    }
}
