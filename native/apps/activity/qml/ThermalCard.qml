import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var zones: eng.activity.thermal || []
    readonly property real hottest: { var m = 0; for (var i = 0; i < zones.length; ++i) m = Math.max(m, zones[i].temp_c); return m }
    function tone(t) { return t > 70 ? Pal.crit : (t > 55 ? Pal.warn : TaosStyle.textSecondary) }
    function word(t) { return t > 70 ? qsTr("hot") : (t > 55 ? qsTr("warm") : qsTr("normal")) }
    title: qsTr("Thermal")
    summary: qsTr("hottest %1°C").arg(hottest.toFixed(1))
    Sparkline {
        Layout.fillWidth: true
        Layout.preferredHeight: 64
        values: c.eng.history.temp || []
        title: qsTr("Hottest zone")
        color: Pal.series1
        maxValue: 100
        format: function (v) { return v.toFixed(1) + "°C" }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: c.width > 420 ? 2 : 1
        columnSpacing: 20
        rowSpacing: 5
        Repeater {
            model: c.zones
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("%1 %2 degrees, %3").arg(modelData.name).arg(modelData.temp_c.toFixed(1)).arg(c.word(modelData.temp_c))
                Txt { text: modelData.name; font.pixelSize: 12; color: TaosStyle.textTertiary; Layout.fillWidth: true }
                Txt { text: c.word(modelData.temp_c); font.pixelSize: 11; color: c.tone(modelData.temp_c) }
                Txt { text: modelData.temp_c.toFixed(1) + "°C"; font.pixelSize: 12; color: c.tone(modelData.temp_c) }
            }
        }
    }
}
