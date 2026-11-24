import QtQuick
import Taos.Bar

// The display menu: per monitor its name, the current mode, a scale control
// (live), the refresh rates at the native resolution and "Use recommended
// settings". While a mode change waits for confirmation the menu shows only
// the "Keep this setting?" question: it cannot be dismissed, Escape and a click
// outside go back to the old mode, and so does the end of the countdown.
MenuFrame {
    id: root
    title: Display.trialActive ? qsTr("Keep this display setting?") : qsTr("Display")
    dismissible: !Display.trialActive
    onDismissAttempt: Display.revert()

    Component.onCompleted: { Display.refresh(); root.focusFirst() }

    function focusFirst() {
        if (Display.trialActive) revertRow.forceActiveFocus()
        else if (Display.outputs.length > 0) root.moveFocus(true)
    }
    Connections {
        target: Display
        function onTrialChanged() {
            if (Display.trialActive && !confirmBox.visible) Qt.callLater(function () { revertRow.forceActiveFocus() })
            else if (!Display.trialActive) Qt.callLater(root.focusFirst)
        }
    }

    // ---- The question, while a mode change is on trial ----
    Column {
        id: confirmBox
        visible: Display.trialActive
        width: parent.width
        spacing: 2
        Text {
            width: parent.width
            leftPadding: 10; rightPadding: 10; topPadding: 6
            text: qsTr("Keep this display setting?")
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }
        Text {
            width: parent.width
            leftPadding: 10; rightPadding: 10; topPadding: 2
            wrapMode: Text.WordWrap
            text: qsTr("%1 now runs at %2.").arg(Display.trialLabel).arg(Display.trialText)
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
        Text {
            width: parent.width
            leftPadding: 10; rightPadding: 10; bottomPadding: 8
            wrapMode: Text.WordWrap
            text: qsTr("If you cannot see this, wait: it goes back by itself in %1 seconds.").arg(Display.trialSecondsLeft)
            color: Theme.textSecondary
            font.family: root.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
        MenuRow {
            id: revertRow
            title: qsTr("Go back")
            subtitle: qsTr("Escape does the same")
            accessibleName: qsTr("Go back to the previous setting")
            onActivated: Display.revert()
        }
        MenuRow {
            id: keepRow
            title: qsTr("Keep this setting")
            accessibleName: qsTr("Keep this display setting")
            emphasized: true
            onActivated: Display.confirm()
        }
    }

    // ---- The settings ----
    Text {
        visible: !Display.trialActive && Display.outputs.length === 0
        width: parent.width
        leftPadding: 10; rightPadding: 10; topPadding: 6; bottomPadding: 8
        wrapMode: Text.WordWrap
        text: qsTr("No display information is available.")
        color: Theme.textSecondary
        font.family: root.fontFamily; font.pixelSize: 12
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Repeater {
        model: Display.trialActive ? 0 : Display.outputs.length
        delegate: Column {
            id: od
            required property int index
            readonly property var o: Display.outputs[od.index] || ({})
            width: parent.width
            spacing: 2

            Rectangle { visible: od.index > 0; width: parent.width; height: 1; color: Theme.stroke; opacity: 0.8 }
            Item {
                width: parent.width
                height: 40
                Column {
                    anchors.left: parent.left; anchors.leftMargin: 10
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    Text {
                        width: parent.width
                        text: od.o.label || ""
                        elide: Text.ElideRight
                        color: Theme.text
                        font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }
                    Text {
                        width: parent.width
                        text: od.o.modeText || ""
                        elide: Text.ElideRight
                        color: Theme.textSecondary
                        font.family: root.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
                        Accessible.role: Accessible.StaticText
                        Accessible.name: qsTr("Current: %1").arg(text)
                    }
                }
            }

            Text {
                leftPadding: 10; topPadding: 2; bottomPadding: 2
                text: qsTr("Scale")
                color: Theme.textSecondary
                font.family: root.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
                Accessible.name: qsTr("Scale for %1").arg(od.o.label || "")
            }
            Flow {
                x: 10
                width: parent.width - 20
                spacing: 6
                Repeater {
                    model: od.o.scales ? od.o.scales.length : 0
                    delegate: ChoiceChip {
                        required property int index
                        readonly property int pct: od.o.scales[index]
                        text: pct + "%"
                        checked: pct === od.o.scalePercent
                        accessibleName: checked ? qsTr("%1 percent, selected").arg(pct) : qsTr("%1 percent").arg(pct)
                        onActivated: Display.setScale(od.o.name, pct / 100)
                    }
                }
            }
            Text {
                visible: Display.xwaylandPresent && Display.anyScaled
                width: parent.width
                leftPadding: 10; rightPadding: 10; topPadding: 4
                wrapMode: Text.WordWrap
                text: qsTr("Older X11 apps are scaled up and can look soft.")
                color: Theme.textTertiary
                font.family: root.fontFamily; font.pixelSize: 11
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }

            Text {
                visible: od.o.rates && od.o.rates.length > 0
                leftPadding: 10; topPadding: 8; bottomPadding: 2
                text: qsTr("Refresh rate")
                color: Theme.textSecondary
                font.family: root.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
                Accessible.name: qsTr("Refresh rate for %1").arg(od.o.label || "")
            }
            Flow {
                visible: od.o.rates && od.o.rates.length > 0
                x: 10
                width: parent.width - 20
                spacing: 6
                Repeater {
                    model: od.o.rates ? od.o.rates.length : 0
                    delegate: ChoiceChip {
                        required property int index
                        readonly property var r: od.o.rates[index]
                        text: r.text
                        checked: r.current
                        accessibleName: checked ? qsTr("%1, selected").arg(r.text) : r.text
                        onActivated: Display.setRefresh(od.o.name, r.mHz)
                    }
                }
            }

            Item { width: 1; height: 6 }
            MenuRow {
                title: qsTr("Use recommended settings")
                subtitle: od.o.recommendedText || ""
                accessibleName: qsTr("Use recommended settings for %1").arg(od.o.label || "")
                emphasized: od.o.isRecommended === true
                onActivated: Display.useRecommended(od.o.name)
                MonitorGlyph { anchors.centerIn: parent; color: od.o.isRecommended === true ? Theme.text : Theme.textSecondary }
                trailing: [ CheckGlyph { visible: od.o.isRecommended === true; anchors.verticalCenter: parent.verticalCenter } ]
            }
        }
    }

    AppearanceSection { visible: !Display.trialActive }

    // ---- Errors, in plain words ----
    Rectangle {
        visible: Display.errorText.length > 0
        width: parent.width
        height: visible ? errLabel.implicitHeight + 16 : 0
        radius: 8
        color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.14)
        Accessible.role: Accessible.AlertMessage
        Accessible.name: Display.errorText
        Text {
            id: errLabel
            anchors.fill: parent; anchors.margins: 8
            wrapMode: Text.WordWrap
            text: Display.errorText
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 12
        }
        MouseArea { anchors.fill: parent; onClicked: Display.clearError() }
    }
    Item { width: 1; height: 2 }

    // Spoken, not shown.
    Item { id: live; width: 1; height: 1; Accessible.role: Accessible.StaticText; Accessible.name: "" }
    Connections { target: Display; function onAnnouncement(t) { MenuHost.announce(live, t) } }
}
