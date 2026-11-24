// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos 1.0
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "about"
    readonly property var about: Settings.about
    property string version: ""
    property string versionState: "loading"   // loading | ready | error

    function loadVersion() {
        versionState = "loading"
        Taos.http("GET", "/api/version", ({}), null, function (ok, value, error) {
            if (ok && value && value.version) { page.version = String(value.version); page.versionState = "ready" }
            else page.versionState = "error"
        })
    }
    Component.onCompleted: { about.refresh(); loadVersion() }

    Group {
        heading: qsTr("This device")
        SRow {
            settingId: "about.device"
            Txt { text: page.about.deviceName; color: TaosStyle.textSecondary; wrapMode: Text.NoWrap; textFormat: Text.PlainText }
        }
        SRow {
            settingId: "about.version"
            note: page.versionState === "error" ? qsTr("The controller did not answer.") : ""
            Txt {
                visible: page.versionState !== "error"
                text: page.versionState === "ready" ? page.version : qsTr("Looking up...")
                color: TaosStyle.textSecondary
                wrapMode: Text.NoWrap
            }
            Btn { visible: page.versionState === "error"; text: qsTr("Try again"); onClicked: page.loadVersion() }
        }
    }
    Group {
        heading: qsTr("Hardware")
        Repeater {
            model: page.about.rows
            delegate: SRow {
                required property var modelData
                title: modelData.label
                Txt { text: modelData.value; color: TaosStyle.textSecondary; wrapMode: Text.NoWrap }
            }
        }
    }
    Group {
        SRow {
            settingId: "about.activity"
            Btn { text: qsTr("Open Activity"); onClicked: page.about.openActivity() }
        }
    }
}
