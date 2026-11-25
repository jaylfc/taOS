// SPDX-License-Identifier: AGPL-3.0-or-later
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

    MoveToGroupDialog { id: moveDialog; engine: page.engine }

    C.ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: col
            x: Math.max(Look.space4, (scroll.availableWidth - width) / 2)
            width: Math.min(scroll.availableWidth - Look.space4 * 2, 940)
            spacing: Look.space4

            PillButton {
                Layout.topMargin: Look.space4
                text: qsTr("Back"); glyph: "arrow-left"; variant: "outline"; compact: true
                objectName: "backButton"
                accessibleName: qsTr("Back to the list")
                onClicked: page.back()
            }

            TaosText {
                visible: !page.known
                text: qsTr("That app is not in the catalogue."); color: Look.textSecondary
            }

            // ---- header: art, icon, name, facts, marks and the action
            DetailHeader {
                visible: page.known
                Layout.fillWidth: true
                name: page.e.name ? page.e.name : ""
                letter: page.e.letter ? page.e.letter : ""
                iconUrl: page.e.iconUrl ? page.e.iconUrl : ""
                coverUrl: page.e.coverUrl ? page.e.coverUrl : ""
                subtitle: [page.e.kind === "webapp" ? qsTr("Web app") : page.e.kind === "model" ? qsTr("Model") : page.e.kind === "agent" ? qsTr("Agent framework")
                           : page.e.kind === "plugin" ? qsTr("Plugin") : page.e.kind === "mcp" ? qsTr("Tool server") : qsTr("App"),
                           page.e.categoryLabel, page.e.version ? "v" + page.e.version : "", page.e.sizeText].filter(s => s).join("  ·  ")
                actions: Component { GetButton { engine: page.engine; entry: page.e; compact: false; showRemove: true; pick: page.variant; hideReview: true; onReview: {} } }
                Chip { visible: page.e.needsDocker === true; text: qsTr("Needs Docker"); tone: "warning"; glyph: "alert" }
                Chip { visible: !!page.e.fit; text: page.e.fitText ? page.e.fitText : ""
                       tone: page.e.fit === "green" ? "success" : page.e.fit === "yellow" ? "warning" : "danger" }
                Chip { visible: page.e.installed === true && page.e.kind !== "webapp"; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                Chip { visible: page.e.updateAvailable === true; text: qsTr("Update available"); tone: "accent" }
                Chip { visible: !!page.e.starsText; text: "★ " + page.e.starsText }
                Chip { visible: !!page.e.license; text: page.e.license ? page.e.license : "" }
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
                    Accessible.name: page.st.consent === "containerapp" ? qsTr("Install %1").arg(page.e.name ? page.e.name : "")
                                     : page.st.consent === "license" ? qsTr("Licence to accept") : qsTr("Docker is needed")
                    TaosText {
                        text: page.st.consent === "containerapp" ? qsTr("Install %1").arg(page.e.name ? page.e.name : "")
                              : page.st.consent === "license" ? qsTr("Licence to accept") : qsTr("Needs Docker")
                        font.weight: Font.DemiBold
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.st.consentText ? page.st.consentText : ""; font.pixelSize: Look.small }
                    // container apps: which group, and the licence when the publisher requires it
                    GroupPicker {
                        visible: page.st.consent === "containerapp"
                        Layout.fillWidth: true
                        engine: page.engine
                        // chosen afresh each time the card is shown, so the row that looks selected is the group used
                        onVisibleChanged: if (visible) reset()
                        onPicked: (g) => page.engine.chooseGroup(page.appId, g)
                    }
                    ColumnLayout {
                        visible: page.st.consent === "containerapp" && page.e.licenceRequired === true
                        objectName: "licenceBlock"
                        Layout.fillWidth: true
                        spacing: 4
                        TaosText { text: qsTr("Licence"); font.pixelSize: Look.small; color: Look.textSecondary }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.weight: Font.DemiBold; text: page.e.license ? page.e.license : "" }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                   visible: !!page.e.termsNote; text: page.e.termsNote ? page.e.termsNote : "" }
                        TaosButton {
                            text: qsTr("Read the licence"); glyph: "external"; variant: "ghost"; compact: true
                            objectName: "readLicence"
                            accessibleName: qsTr("Read the licence of %1 in your browser").arg(page.e.name ? page.e.name : "")
                            onClicked: Qt.openUrlExternally(page.e.licenceUrl)
                        }
                    }
                    RowLayout {
                        spacing: 8
                        TaosButton {
                            objectName: "consentAccept"
                            text: page.st.consent === "containerapp" ? (page.e.licenceRequired === true ? qsTr("I agree and install") : qsTr("Install"))
                                  : page.st.consent === "license" ? qsTr("I agree and install") : qsTr("Install with Docker")
                            variant: "primary"
                            onClicked: page.engine.acceptConsent(page.appId)
                        }
                        TaosButton { objectName: "consentCancel"; text: qsTr("Cancel"); variant: "ghost"; onClicked: page.engine.cancelConsent(page.appId) }
                    }
                }
            }

            // ---- container app: where it lives, move, licence, updates
            ColumnLayout {
                visible: page.known && page.e.kind === "containerapp" && page.e.installed === true
                objectName: "groupSection"
                Layout.fillWidth: true
                spacing: 6
                TaosText { text: qsTr("Where it lives"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textSecondary
                           font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: groupFlow.implicitHeight + 24
                    radius: Look.radiusCard; color: Look.surface
                    border.width: 1; border.color: Look.hairline
                    Flow {
                        id: groupFlow
                        x: 14; y: 12
                        width: parent.width - 28
                        spacing: 12
                        ColumnLayout {
                            width: Math.max(180, groupFlow.width - moveBtn.width - groupFlow.spacing)
                            spacing: 2
                            TaosText { objectName: "groupName"; text: page.e.groupName ? page.e.groupName : ""; font.weight: Font.DemiBold
                                       Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: qsTr("App group: %1").arg(text) }
                            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                       text: [page.e.installedVersion ? qsTr("Version %1").arg(page.e.installedVersion) : "", page.e.licenceAcceptedText ? page.e.licenceAcceptedText : ""].filter(x => x).join("  ·  ") }
                        }
                        TaosButton {
                            id: moveBtn
                            objectName: "moveToGroup"
                            text: qsTr("Move to group…"); variant: "outline"; compact: true
                            enabled: page.st.state === "idle" || page.st.state === "installed"
                            accessibleName: qsTr("Move %1 to another app group").arg(page.e.name ? page.e.name : "")
                            onClicked: moveDialog.openFor(page.appId, page.e.name, page.e.group)
                        }
                    }
                }
                RowLayout {
                    visible: page.e.canUpdate === true
                    Layout.fillWidth: true
                    spacing: 8
                    readonly property var up: page.engine.revision >= 0 ? page.engine.updateState(page.appId) : ({})
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: parent.up.text ? parent.up.text : qsTr("Updates come from the publisher when you ask for them.")
                               Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text }
                    TaosButton {
                        objectName: "updateButton"
                        readonly property string us: parent.up.state ? parent.up.state : "idle"
                        text: us === "available" ? qsTr("Update") : qsTr("Check for updates")
                        variant: us === "available" ? "primary" : "outline"; compact: true
                        enabled: us !== "checking" && us !== "updating" && (page.st.state === "idle" || page.st.state === "installed")
                        accessibleName: us === "available" ? qsTr("Install the new version of %1").arg(page.e.name) : qsTr("Check whether %1 has an update").arg(page.e.name)
                        onClicked: us === "available" ? page.engine.updateContainerApp(page.appId) : page.engine.checkAppUpdate(page.appId)
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
            TaosText { visible: page.known && !!page.e.description; text: qsTr("About"); font.pixelSize: Look.small; font.weight: Font.DemiBold
                       color: Look.textSecondary; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText {
                visible: page.known
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: page.e.description ? page.e.description : ""
                font.pixelSize: Look.bodyLarge
                lineHeight: 1.3
                color: Look.text
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
                    radius: Look.radiusCard; color: Look.surface; clip: true
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
