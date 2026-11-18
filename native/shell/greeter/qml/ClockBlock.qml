import QtQuick
import QtQuick.Effects
import Taos.Greeter

// Clock, date and weather, centred on the vertical axis of the wordmark.
Item {
    id: cb
    property date now: new Date()
    readonly property real clockPx: Plan.clockPx
    Timer {
        // Fires just after each minute boundary; a 1 minute resolution clock
        // wakes the process once a minute and not more.
        id: tick
        running: true
        repeat: false
        interval: 1000
        onTriggered: {
            cb.now = new Date();
            interval = 60000 - (cb.now.getSeconds() * 1000 + cb.now.getMilliseconds()) + 40;
            restart();
        }
    }
    Component.onCompleted: tick.triggered()

    Text {
        id: time
        anchors.horizontalCenter: parent.horizontalCenter
        y: -cb.clockPx * 0.08
        text: Greeter.clock24 ? Qt.formatTime(cb.now, "HH:mm") : Qt.locale().toString(cb.now, "h:mm")
        color: Style.text
        font.family: "Inter"
        font.weight: Font.ExtraLight
        font.pixelSize: cb.clockPx
        font.letterSpacing: -cb.clockPx * 0.03
        font.features: { "tnum": 1, "ss01": 0 }
        renderType: Text.QtRendering
        lineHeightMode: Text.FixedHeight
        lineHeight: cb.clockPx * 1.0
        layer.enabled: true
        layer.smooth: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "black"
            shadowOpacity: Style.dark ? 0.40 : 0.10
            shadowBlur: 1.0
            shadowVerticalOffset: Plan.clockPx * 0.02
        }
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("Time %1").arg(text)
    }
    Text {
        id: date
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: time.bottom
        anchors.topMargin: -cb.clockPx * 0.02
        text: Qt.locale().toString(cb.now, "dddd d MMMM")
        color: Style.text
        opacity: 0.92
        font.family: "Inter"
        font.weight: Font.Medium
        font.pixelSize: Plan.datePx
        font.letterSpacing: Plan.datePx * 0.005
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Row {
        id: wx
        visible: Weather.available
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: date.bottom
        anchors.topMargin: Plan.weatherPx * 0.9
        spacing: Plan.weatherPx * 0.55
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("Weather: %1, %2%3").arg(Weather.temperature).arg(Weather.condition)
                         .arg(Weather.place ? qsTr(", %1").arg(Weather.place) : "")
        WeatherIcon {
            anchors.verticalCenter: parent.verticalCenter
            width: Plan.weatherPx * 1.75; height: width
            icon: Weather.icon
            color: Style.text
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: Weather.temperature
            color: Style.text
            font.family: "Inter"; font.weight: Font.Light
            font.pixelSize: Plan.weatherPx * 1.55
            font.features: { "tnum": 1 }
        }
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 1; height: Plan.weatherPx * 1.2
            color: Style.text; opacity: 0.35
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: Weather.place ? Weather.condition + "  ·  " + Weather.place : Weather.condition
            color: Style.textSecondary
            font.family: "Inter"; font.weight: Font.Medium
            font.pixelSize: Plan.weatherPx
        }
    }
}
