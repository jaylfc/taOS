import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One app: what it is, what it asks for, and its install state with a clear reason when something goes wrong.
Item {
    id: page
    property var engine
    property string appId: ""
    signal back()
    signal gotoSection(string section)
    readonly property var e: engine.revision >= 0 ? engine.app(appId) : ({})
    readonly property var st: engine.revision >= 0 ? engine.installState(appId) : ({})
    readonly property var asks: engine.revision >= 0 ? engine.asks(appId) : []
    readonly property bool known: e.id !== undefined
    property string variant: ""
    onAppIdChanged: { variant = ""; engine.loadDetail(appId) }
    Component.onCompleted: engine.loadDetail(appId)
    Keys.onEscapePressed: page.back()

    C.ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: Math.max(20, (parent.width - width) / 2)
            width: Math.min(parent.width - 40, 720)
            spacing: 18

            TaosButton {
                Layout.topMargin: 16
                text: qsTr("Back"); variant: "ghost"; compact: true
                objectName: "backButton"
                accessibleName: qsTr("Back to the list")
                onClicked: page.back()
            }

            TaosText {
                visible: !page.known
                text: qsTr("That app is not in the catalogue."); color: Look.textSecondary
            }

            // ---- header
            RowLayout {
                visible: page.known
                spacing: 16
                AppIcon { size: 72; letter: page.e.letter ? page.e.letter : "?" }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    TaosText { text: page.e.name ? page.e.name : ""; font.pixelSize: Look.heading + 4; font.weight: Font.DemiBold
                               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                    TaosText {
                        text: [page.e.kind === "webapp" ? qsTr("Web app") : page.e.kind === "model" ? qsTr("Model") : page.e.kind === "agent" ? qsTr("Agent framework")
                               : page.e.kind === "plugin" ? qsTr("Plugin") : page.e.kind === "mcp" ? qsTr("Tool server") : qsTr("App"),
                               page.e.category, page.e.version ? "v" + page.e.version : "", page.e.sizeText].filter(s => s).join("  ·  ")
                        color: Look.textSecondary; font.pixelSize: Look.small; font.features: { "tnum": 1 }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        Chip { visible: page.e.needsDocker === true; text: qsTr("Needs Docker"); tone: "warning"; glyph: "alert" }
                        Chip { visible: !!page.e.fit; text: page.e.fitText ? page.e.fitText : ""
                               tone: page.e.fit === "green" ? "success" : page.e.fit === "yellow" ? "warning" : "danger" }
                        Chip { visible: page.e.installed === true && page.e.kind !== "webapp"; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                        Chip { visible: page.e.updateAvailable === true; text: qsTr("Update available"); tone: "accent" }
                    }
                }
                StateButton { engine: page.engine; entry: page.e; compact: false; showRemove: true; pick: page.variant; onReview: {} }
            }

            // ---- model variant picker
            ColumnLayout {
                visible: page.known && page.e.modelVariants !== undefined && page.e.modelVariants.length > 1 && !page.e.installed
                Layout.fillWidth: true; spacing: 4
                TaosText { text: qsTr("Version to download"); font.pixelSize: Look.small; color: Look.textSecondary }
                TaosCombo {
                    id: variantBox
                    objectName: "variantBox"
                    Layout.preferredWidth: 360
                    model: page.e.modelVariants ? [{ id: "", label: qsTr("Best for this device (automatic)") }].concat(page.e.modelVariants.map(v => ({ id: v.id, label: v.name + (v.sizeText ? "  " + v.sizeText : "") + (v.fitText ? "  ·  " + v.fitText : "") }))) : []
                    textRole: "label"
                    onActivated: (i) => page.variant = model[i].id
                    accessibleName: qsTr("Version to download")
                }
            }

            // ---- consent (Docker, or a licence the controller asks you to accept)
            Rectangle {
                visible: page.st.state === "consent"
                objectName: "consentCard"
                Layout.fillWidth: true
                implicitHeight: consentCol.implicitHeight + 28
                radius: Look.radiusControl
                color: Look.tint(Look.warning, 0.10)
                border.width: 1; border.color: Look.tint(Look.warning, 0.32)
                ColumnLayout {
                    id: consentCol
                    anchors { fill: parent; margins: 14 }
                    spacing: 10
                    Accessible.role: Accessible.Dialog
                    Accessible.name: page.st.consent === "license" ? qsTr("Licence to accept") : qsTr("Docker is needed")
                    TaosText {
                        text: page.st.consent === "license" ? qsTr("Licence to accept") : qsTr("Needs Docker")
                        font.weight: Font.DemiBold
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.st.consentText ? page.st.consentText : ""; font.pixelSize: Look.small }
                    RowLayout {
                        spacing: 8
                        TaosButton {
                            objectName: "consentAccept"
                            text: page.st.consent === "license" ? qsTr("I agree and install") : qsTr("Install with Docker")
                            variant: "primary"
                            onClicked: page.engine.acceptConsent(page.appId)
                        }
                        TaosButton { objectName: "consentCancel"; text: qsTr("Cancel"); variant: "ghost"; onClicked: page.engine.cancelConsent(page.appId) }
                    }
                }
            }

            // ---- failure, in words
            Rectangle {
                visible: page.st.state === "failed"
                objectName: "failureCard"
                Layout.fillWidth: true
                implicitHeight: failCol.implicitHeight + 28
                radius: Look.radiusControl
                color: Look.tint(Look.danger, 0.10)
                border.width: 1; border.color: Look.tint(Look.danger, 0.32)
                ColumnLayout {
                    id: failCol
                    anchors { fill: parent; margins: 14 }
                    spacing: 8
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: page.st.error ? page.st.error : ""
                    TaosText { text: qsTr("That did not work"); font.weight: Font.DemiBold }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.st.error ? page.st.error : ""; font.pixelSize: Look.small }
                    TaosButton { text: qsTr("Dismiss"); variant: "ghost"; compact: true; onClicked: page.engine.dismiss(page.appId) }
                }
            }

            // ---- about
            TaosText {
                visible: page.known
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: page.e.description ? page.e.description : ""
                font.pixelSize: Look.bodyLarge
                lineHeight: 1.25
            }

            // ---- what it asks for
            ColumnLayout {
                visible: page.known && page.asks.length > 0
                Layout.fillWidth: true; spacing: 6
                TaosText { text: qsTr("What it needs"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textSecondary
                           font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: asksCol.implicitHeight
                    radius: 10; color: Look.surface; clip: true
                    ColumnLayout {
                        id: asksCol
                        width: parent.width
                        spacing: 0
                        Repeater {
                            model: page.asks
                            delegate: Item {
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                implicitHeight: Math.max(40, v.implicitHeight + 20)
                                Accessible.role: Accessible.StaticText
                                Accessible.name: modelData.label + ": " + modelData.value
                                TaosText { x: 14; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: Look.textSecondary; width: 110 }
                                TaosText { id: v; x: 130; width: parent.width - 144; anchors.verticalCenter: parent.verticalCenter
                                           text: modelData.value; wrapMode: Text.Wrap; font.features: { "tnum": 1 } }
                                Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 14 } height: 1
                                            color: Look.hairline; visible: index < page.asks.length - 1 }
                            }
                        }
                    }
                    Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
                }
            }

            // ---- web app: your copies
            ColumnLayout {
                visible: page.known && page.e.kind === "webapp" && page.e.instanceRows !== undefined && page.e.instanceRows.length > 0
                Layout.fillWidth: true; spacing: 6
                TaosText { text: qsTr("You have %1").arg(page.e.instanceRows ? page.e.instanceRows.length : 0); font.pixelSize: Look.small; font.weight: Font.DemiBold
                           color: Look.textSecondary; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6 }
                Repeater {
                    model: page.e.instanceRows ? page.e.instanceRows : []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 10
                        TaosText { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                        TaosText { text: modelData.sizeText + "  ·  " + modelData.lastUsedText; color: Look.textSecondary; font.pixelSize: Look.small }
                        TaosButton { text: qsTr("Open"); compact: true; variant: "primary"; onClicked: page.engine.webapps.launch(modelData.id)
                                     accessibleName: qsTr("Open %1").arg(modelData.name) }
                    }
                }
                TaosButton { text: qsTr("Manage in Web apps"); variant: "ghost"; compact: true; onClicked: page.gotoSection("webapps") }
            }

            Item { Layout.preferredHeight: 20 }
        }
    }
}
