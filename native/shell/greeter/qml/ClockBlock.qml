// SPDX-License-Identifier: AGPL-3.0-or-later
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

    // One quiet line above the time: the date, then the weather as an icon
    // and a temperature. The condition and the place are spoken, not shown.
    Row {
        id: info
        anchors.horizontalCenter: parent.horizontalCenter
        y: 0
        height: Plan.datePx * 1.3
        spacing: Plan.datePx * 0.6
        Text {
            id: date
            anchors.verticalCenter: parent.verticalCenter
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
            anchors.verticalCenter: parent.verticalCenter
            spacing: Plan.datePx * 0.3
            Accessible.role: Accessible.StaticText
            Accessible.name: qsTr("Weather: %1, %2%3").arg(Weather.temperature).arg(Weather.condition)
                             .arg(Weather.place ? qsTr(", %1").arg(Weather.place) : "")
            WeatherIcon {
                anchors.verticalCenter: parent.verticalCenter
                width: Plan.datePx * 1.25; height: width
                icon: Weather.icon
                color: Style.text
                opacity: 0.92
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: Weather.temperature
                color: Style.text
                opacity: 0.92
                font.family: "Inter"; font.weight: Font.Medium
                font.pixelSize: Plan.datePx
                font.features: { "tnum": 1 }
            }
        }
    }
    Text {
        id: time
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: info.bottom
        anchors.topMargin: -cb.clockPx * 0.06
        text: Greeter.clock24 ? Qt.formatTime(cb.now, "HH:mm") : Qt.locale().toString(cb.now, "h:mm")
        color: Style.text
        font.family: "Inter"
        font.weight: Font.Light
        font.pixelSize: cb.clockPx
        font.letterSpacing: -cb.clockPx * 0.035
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
}
