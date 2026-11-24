import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// Filters, live indicator, refresh and the event list. Used inside the card on
// desktop and on the pushed page on a phone.
ColumnLayout {
    id: p
    property var feed
    property double nowSec: 0
    property bool compact: false
    property int listHeight: 320
    spacing: Pal.gap
    Layout.fillWidth: true

    function entries(first, values, labelFn) {
        var out = [{ text: first, value: "" }]
        for (var i = 0; i < values.length; ++i)
            out.push({ text: labelFn ? labelFn(values[i]) : values[i], value: values[i] })
        return out
    }

    Flow {
        Layout.fillWidth: true
        spacing: 8
        FilterBox {
            name: qsTr("Filter by worker")
            model: p.entries(qsTr("All workers"), p.feed.workerOptions)
            current: p.feed.worker
            onPicked: function (v) { p.feed.worker = v }
        }
        FilterBox {
            name: qsTr("Filter by model")
            model: p.entries(qsTr("All models"), p.feed.modelOptions)
            current: p.feed.modelFilter
            onPicked: function (v) { p.feed.modelFilter = v }
        }
        FilterBox {
            name: qsTr("Filter by event type")
            model: p.entries(qsTr("All events"), p.feed.eventTypes, Fmt.eventLabel)
            current: p.feed.eventType
            onPicked: function (v) { p.feed.eventType = v }
        }
        GlassButton {
            text: "↻"
            implicitWidth: 32; implicitHeight: 30
            padding: 0; leftPadding: 0; rightPadding: 0
            Accessible.name: qsTr("Refresh model activity")
            onClicked: p.feed.reload()
        }
    }
    Txt {
        visible: p.feed.errorText.length > 0
        text: p.feed.errorText
        font.pixelSize: 12
        color: p.feed.signInNeeded ? TaosStyle.textSecondary : Pal.crit
        Layout.fillWidth: true
        elide: Text.ElideNone
        wrapMode: Text.WordWrap
        Accessible.role: Accessible.AlertMessage
    }
    Txt {
        visible: p.feed.count === 0 && !p.feed.signInNeeded
        text: p.feed.loading ? qsTr("Loading…") : qsTr("No model activity yet.")
        font.pixelSize: 12
        color: TaosStyle.textTertiary
    }
    ModelActivityList {
        Layout.fillWidth: true
        Layout.preferredHeight: p.feed.count === 0 ? 0 : p.listHeight
        visible: p.feed.count > 0
        feed: p.feed
        nowSec: p.nowSec
        compact: p.compact
    }
}
