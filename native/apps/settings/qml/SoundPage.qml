// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "sound"
    readonly property var audio: Settings.audio
    phase: !audio.available ? "error" : "ready"
    message: qsTr("The sound system is not answering. Sound settings appear when it is back.")
    canRetry: false

    property int outIdx: Settings.activeRow(audio.outputs)
    property int inIdx: Settings.activeRow(audio.inputs)
    Connections {
        target: page.audio
        function onChanged() { page.outIdx = Settings.activeRow(page.audio.outputs); page.inIdx = Settings.activeRow(page.audio.inputs) }
    }
    Txt {
        width: parent.width
        visible: page.audio.errorText.length > 0
        text: page.audio.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }
    Txt {
        width: parent.width
        visible: page.audio.message.length > 0
        text: page.audio.message
        color: TaosStyle.textSecondary
    }
    Group {
        heading: qsTr("Output")
        visible: page.audio.hasOutput
        SRow {
            settingId: "sound.output"
            Picker {
                label: qsTr("Output")
                model: page.audio.outputs
                textRole: "label"
                currentIndex: page.outIdx
                onActivated: (i) => page.audio.selectOutput(page.audio.outputs.data(page.audio.outputs.index(i, 0), Qt.UserRole + 1))
            }
        }
        SRow {
            settingId: "sound.volume"
            description: qsTr("%1%").arg(page.audio.percent)
            SliderCtl { label: qsTr("Volume"); value: page.audio.percent; onChangedByPerson: (v) => page.audio.setPercent(v) }
        }
        SRow {
            settingId: "sound.mute"
            Toggle { label: qsTr("Mute"); checked: page.audio.muted; onFlipped: (on) => page.audio.setMuted(on) }
        }
    }
    Group {
        heading: qsTr("Input")
        visible: page.audio.hasInput
        SRow {
            settingId: "sound.input"
            Picker {
                label: qsTr("Input")
                model: page.audio.inputs
                textRole: "label"
                currentIndex: page.inIdx
                onActivated: (i) => page.audio.selectInput(page.audio.inputs.data(page.audio.inputs.index(i, 0), Qt.UserRole + 1))
            }
        }
        SRow {
            settingId: "sound.inputVolume"
            description: qsTr("%1%").arg(page.audio.inputPercent)
            SliderCtl { label: qsTr("Input volume"); value: page.audio.inputPercent; onChangedByPerson: (v) => page.audio.setInputPercent(v) }
        }
        SRow {
            settingId: "sound.inputMute"
            Toggle { label: qsTr("Mute microphone"); checked: page.audio.inputMuted; onFlipped: (on) => page.audio.setInputMuted(on) }
        }
    }
    Txt {
        width: parent.width
        visible: !page.audio.hasOutput && !page.audio.hasInput
        text: qsTr("No sound devices were found.")
        color: TaosStyle.textSecondary
    }
}
