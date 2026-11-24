// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The sound menu: output volume and mute, the outputs that are plugged in (a
// tick on the current one), a profile choice where it matters (Bluetooth
// music or headset, analog or HDMI), then the microphone with a live level.
MenuFrame {
    id: root
    title: qsTr("Sound")

    Component.onCompleted: forceActiveFocus()

    // ---- Output ----
    Item {
        width: parent.width
        height: 32
        Text {
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Output")
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }
    }
    Text {
        visible: Audio.message.length > 0 || !Audio.hasOutput
        width: parent.width
        leftPadding: 10; rightPadding: 10; bottomPadding: 6
        wrapMode: Text.WordWrap
        text: Audio.message.length > 0 ? Audio.message : qsTr("No sound output found. Plug in speakers or headphones, or connect a Bluetooth device.")
        color: Theme.textSecondary
        font.family: root.fontFamily; font.pixelSize: 12
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Row {
        visible: Audio.hasOutput
        width: parent.width
        height: 30
        leftPadding: 12; rightPadding: 12
        spacing: 10
        MuteButton { anchors.verticalCenter: parent.verticalCenter; glyph: "speaker"; level: Audio.level; muted: Audio.muted; label: qsTr("Mute output"); onToggled: function (m) { Audio.setMuted(m) } }
        LevelSlider {
            width: parent.width - 24 - 28 - 44 - 20
            anchors.verticalCenter: parent.verticalCenter
            label: qsTr("Output volume")
            value: Audio.percent
            dimmed: Audio.muted
            onMoved: function (v) { Audio.setPercent(v) }
        }
        Text {
            width: 44
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignRight
            text: Audio.muted ? qsTr("Muted") : Audio.percent + "%"
            color: Theme.textSecondary
            font.family: root.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
            Accessible.ignored: true
        }
    }
    Repeater {
        model: Audio.available ? Audio.outputs : null
        delegate: MenuRow {
            id: od
            required property string choiceId
            required property string label
            required property string detail
            required property string icon
            required property bool active
            required property string description
            title: od.label
            subtitle: od.detail
            accessibleName: od.description
            emphasized: od.active
            onActivated: Audio.selectOutput(od.choiceId)
            DeviceGlyph { kind: od.icon; anchors.centerIn: parent; color: od.active ? Theme.text : Theme.textSecondary }
            trailing: [ CheckGlyph { visible: od.active; anchors.verticalCenter: parent.verticalCenter } ]
        }
    }
    // Profile: only when there is something to choose.
    Item {
        visible: Audio.outputProfileCount > 0
        width: parent.width
        height: visible ? 26 : 0
        Text {
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Mode for %1").arg(Audio.outputProfileTitle)
            elide: Text.ElideRight
            width: parent.width - 20
            color: Theme.textSecondary
            font.family: root.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }
    }
    Repeater {
        model: Audio.outputProfiles
        delegate: MenuRow {
            id: pd
            required property int card
            required property string name
            required property string label
            required property bool active
            title: pd.label
            accessibleName: pd.active ? qsTr("%1, selected").arg(pd.label) : pd.label
            emphasized: pd.active
            onActivated: Audio.selectProfile(pd.card, pd.name, true)
            trailing: [ CheckGlyph { visible: pd.active; anchors.verticalCenter: parent.verticalCenter } ]
        }
    }

    Rectangle { visible: Audio.available; width: parent.width; height: 1; color: Theme.stroke; opacity: 0.8 }

    // ---- Input ----
    Item {
        visible: Audio.available
        width: parent.width
        height: visible ? 32 : 0
        Text {
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Input")
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }
    }
    Text {
        visible: Audio.available && !Audio.hasInput
        width: parent.width
        leftPadding: 10; rightPadding: 10; bottomPadding: 6
        wrapMode: Text.WordWrap
        text: qsTr("No microphone found")
        color: Theme.textSecondary
        font.family: root.fontFamily; font.pixelSize: 12
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Column {
        visible: Audio.hasInput
        width: parent.width
        spacing: 4
        Row {
            width: parent.width
            height: 22
            leftPadding: 12; rightPadding: 12
            spacing: 10
            Item { width: 28; height: 1 }
            LevelMeter {
                width: parent.width - 24 - 28 - 10
                anchors.verticalCenter: parent.verticalCenter
                level: Audio.micLevel
                muted: Audio.inputMuted
            }
        }
        Row {
            width: parent.width
            height: 28
            leftPadding: 12; rightPadding: 12
            spacing: 10
            MuteButton { anchors.verticalCenter: parent.verticalCenter; glyph: "mic"; muted: Audio.inputMuted; label: qsTr("Mute microphone"); onToggled: function (m) { Audio.setInputMuted(m) } }
            LevelSlider {
                width: parent.width - 24 - 28 - 44 - 20
                anchors.verticalCenter: parent.verticalCenter
                label: qsTr("Input volume")
                value: Audio.inputPercent
                dimmed: Audio.inputMuted
                onMoved: function (v) { Audio.setInputPercent(v) }
            }
            Text {
                width: 44
                anchors.verticalCenter: parent.verticalCenter
                horizontalAlignment: Text.AlignRight
                text: Audio.inputPercent + "%"
                color: Theme.textSecondary
                font.family: root.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
                Accessible.ignored: true
            }
        }
    }
    Repeater {
        model: Audio.available ? Audio.inputs : null
        delegate: MenuRow {
            id: id_
            required property string choiceId
            required property string label
            required property string detail
            required property string icon
            required property bool active
            required property string description
            title: id_.label
            subtitle: id_.detail
            accessibleName: id_.description
            emphasized: id_.active
            onActivated: Audio.selectInput(id_.choiceId)
            DeviceGlyph { kind: id_.icon; anchors.centerIn: parent; color: id_.active ? Theme.text : Theme.textSecondary }
            trailing: [ CheckGlyph { visible: id_.active; anchors.verticalCenter: parent.verticalCenter } ]
        }
    }
    Item {
        visible: Audio.inputProfileCount > 0
        width: parent.width
        height: visible ? 26 : 0
        Text {
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Mode for %1").arg(Audio.inputProfileTitle)
            elide: Text.ElideRight
            width: parent.width - 20
            color: Theme.textSecondary
            font.family: root.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
        }
    }
    Repeater {
        model: Audio.inputProfiles
        delegate: MenuRow {
            id: ip
            required property int card
            required property string name
            required property string label
            required property bool active
            title: ip.label
            accessibleName: ip.active ? qsTr("%1, selected").arg(ip.label) : ip.label
            emphasized: ip.active
            onActivated: Audio.selectProfile(ip.card, ip.name, false)
            trailing: [ CheckGlyph { visible: ip.active; anchors.verticalCenter: parent.verticalCenter } ]
        }
    }

    // ---- Errors, in plain words ----
    Rectangle {
        id: errBox
        visible: Audio.errorText.length > 0
        width: parent.width
        height: visible ? errLabel.implicitHeight + 16 : 0
        radius: 8
        color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.14)
        Accessible.role: Accessible.AlertMessage
        Accessible.name: Audio.errorText
        Text {
            id: errLabel
            anchors.fill: parent; anchors.margins: 8
            wrapMode: Text.WordWrap
            text: Audio.errorText
            color: Theme.text
            font.family: root.fontFamily; font.pixelSize: 12
        }
        MouseArea { anchors.fill: parent; onClicked: Audio.clearError() }
    }
    Item { width: 1; height: 2 }

    // Spoken, not shown.
    Item { id: live; width: 1; height: 1; Accessible.role: Accessible.StaticText; Accessible.name: "" }
    Connections { target: Audio; function onAnnouncement(t) { MenuHost.announce(live, t) } }
}
