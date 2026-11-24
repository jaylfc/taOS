import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Web apps: the instances you have (open, rename, clone with the sign-in, clear data, remove) and the catalogue
// of sites you can add. Every instance has its own storage and sign-in.
Item {
    id: page
    property var engine
    property string query: ""
    property string selected: ""
    signal openDetail(string id)
    signal addWebsite()
    readonly property var web: engine.webapps
    readonly property var mine: engine.revision >= 0 ? web.instances : []
    readonly property var catalogue: engine.revision >= 0 ? engine.list("webapps", query, "") : []
    property string confirm: ""        // "<action>:<id>" waiting for a second click
    property string renaming: ""
    onSelectedChanged: { confirm = ""; renaming = "" }

    function matches(m) { return query === "" || (m.name + " " + m.host).toLowerCase().indexOf(query.toLowerCase()) >= 0 }

    C.ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 20; Layout.bottomMargin: 6
                spacing: 12
                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true
                    TaosText { text: qsTr("Web apps"); font.pixelSize: Look.heading; font.weight: Font.DemiBold
                               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                    TaosText {
                        Layout.fillWidth: true
                        text: qsTr("A website in its own window, with its own storage and sign-in. Add a site again to use a second account.")
                        wrapMode: Text.Wrap; color: Look.textSecondary; font.pixelSize: Look.small
                    }
                }
                TaosButton {
                    objectName: "addWebsiteButton"
                    text: qsTr("Add website")
                    glyph: "plus"
                    variant: "primary"
                    accessibleName: qsTr("Add any website as a web app")
                    onClicked: page.addWebsite()
                }
            }

            // no browser engine: said plainly, with the fix
            Rectangle {
                visible: !page.web.browserAvailable
                objectName: "noBrowserBanner"
                Layout.fillWidth: true
                Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.topMargin: 8
                implicitHeight: bannerText.implicitHeight + 20
                radius: Look.radiusControl
                color: Look.tint(Look.warning, 0.12)
                border.width: 1; border.color: Look.tint(Look.warning, 0.32)
                Accessible.role: Accessible.AlertMessage
                Accessible.name: bannerText.text
                TaosText {
                    id: bannerText
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
                    text: page.web.browserText !== "" ? page.web.browserText : qsTr("No browser engine installed. Web apps need Chromium.")
                    wrapMode: Text.Wrap
                    font.pixelSize: Look.small
                }
            }

            // ---- yours
            TaosText {
                visible: page.mine.length > 0
                Layout.leftMargin: 24; Layout.topMargin: 18; Layout.bottomMargin: 6
                text: qsTr("Yours"); font.pixelSize: Look.small; font.weight: Font.DemiBold
                color: Look.textSecondary; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
            }
            Rectangle {
                visible: page.mine.length > 0
                Layout.fillWidth: true
                Layout.leftMargin: 20; Layout.rightMargin: 20
                implicitHeight: instCol.implicitHeight
                radius: 10; color: Look.surface; clip: true
                ColumnLayout {
                    id: instCol
                    width: parent.width
                    spacing: 0
                    Repeater {
                        model: page.mine
                        delegate: ColumnLayout {
                            id: inst
                            required property var modelData
                            required property int index
                            readonly property bool open: page.selected === modelData.id
                            readonly property bool busy: page.engine.revision >= 0 && page.web.isBusy(modelData.id)
                            visible: page.matches(modelData)
                            Layout.fillWidth: true
                            spacing: 0
                            C.ItemDelegate {
                                Layout.fillWidth: true
                                implicitHeight: 60
                                hoverEnabled: true
                                objectName: "instance-" + inst.modelData.id
                                Accessible.role: Accessible.ListItem
                                Accessible.name: inst.modelData.name + ", " + inst.modelData.host + ", " + inst.modelData.sizeText + ", " + inst.modelData.lastUsedText
                                onClicked: page.selected = inst.open ? "" : inst.modelData.id
                                background: Rectangle {
                                    color: parent.down ? Look.pressed : parent.hovered || parent.visualFocus ? Look.hover : "transparent"
                                    Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 64 } height: 1; color: Look.hairline
                                                visible: inst.index < page.mine.length - 1 || inst.open }
                                }
                                contentItem: RowLayout {
                                    spacing: 12
                                    AppIcon { Layout.leftMargin: 12; size: 36; letter: inst.modelData.letter; iconUrl: inst.modelData.iconUrl }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 1
                                        TaosText { text: inst.modelData.name; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                                        TaosText { text: inst.modelData.host + "  ·  " + inst.modelData.sizeText + "  ·  " + inst.modelData.lastUsedText
                                                   color: Look.textSecondary; font.pixelSize: Look.small; elide: Text.ElideRight; Layout.fillWidth: true
                                                   font.features: { "tnum": 1 } }
                                    }
                                    Chip { visible: inst.modelData.running; text: qsTr("Open"); tone: "success"; accessibleName: qsTr("Window is open") }
                                    TaosText { visible: inst.busy; text: page.web.busyText(inst.modelData.id); color: Look.textSecondary; font.pixelSize: Look.small }
                                    TaosButton {
                                        text: qsTr("Open"); variant: "primary"; compact: true; Layout.rightMargin: 12
                                        enabled: !inst.busy
                                        accessibleName: qsTr("Open %1").arg(inst.modelData.name)
                                        onClicked: page.web.launch(inst.modelData.id)
                                    }
                                }
                            }
                            // the expanded strip: facts and the actions
                            Item {
                                visible: inst.open
                                Layout.fillWidth: true
                                implicitHeight: visible ? strip.implicitHeight + 16 : 0
                                ColumnLayout {
                                    id: strip
                                    anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 64; rightMargin: 16; topMargin: 4 }
                                    spacing: 10
                                    TaosText {
                                        Layout.fillWidth: true; wrapMode: Text.Wrap
                                        text: qsTr("%1 on disk. Window id %2.").arg(inst.modelData.sizeText).arg(inst.modelData.app_id)
                                        color: Look.textSecondary; font.pixelSize: Look.small
                                    }
                                    RowLayout {
                                        visible: page.renaming === inst.modelData.id
                                        spacing: 8
                                        TaosField { id: renameField; objectName: "renameField"; Layout.preferredWidth: 220; text: inst.modelData.name
                                                    accessibleName: qsTr("New name"); onAccepted: renameGo.clicked()
                                                    onVisibleChanged: if (visible) { forceActiveFocus(); selectAll() } }
                                        TaosButton { id: renameGo; objectName: "renameSave"; text: qsTr("Save"); variant: "primary"; compact: true
                                                     onClicked: { page.web.rename(inst.modelData.id, renameField.text); page.renaming = "" } }
                                        TaosButton { text: qsTr("Cancel"); variant: "ghost"; compact: true; onClicked: page.renaming = "" }
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        visible: page.confirm === "" || page.confirm.split(":")[1] !== inst.modelData.id
                                        TaosButton { objectName: "renameButton"; text: qsTr("Rename"); compact: true; enabled: !inst.busy
                                                     onClicked: page.renaming = inst.modelData.id }
                                        TaosButton { objectName: "cloneButton"; text: qsTr("Clone"); compact: true; enabled: !inst.busy
                                                     accessibleName: qsTr("Clone %1, copying its sign-in").arg(inst.modelData.name)
                                                     onClicked: page.web.clone(inst.modelData.id) }
                                        TaosButton { objectName: "clearButton"; text: qsTr("Clear data"); compact: true; enabled: !inst.busy
                                                     onClicked: page.confirm = "clear:" + inst.modelData.id }
                                        TaosButton { objectName: "removeButton"; text: qsTr("Remove"); variant: "danger"; compact: true; enabled: !inst.busy
                                                     onClicked: page.confirm = "remove:" + inst.modelData.id }
                                    }
                                    RowLayout {
                                        visible: page.confirm !== "" && page.confirm.split(":")[1] === inst.modelData.id
                                        spacing: 8
                                        TaosText {
                                            Layout.fillWidth: true; wrapMode: Text.Wrap
                                            text: page.confirm.indexOf("clear:") === 0
                                                  ? qsTr("Sign out and delete everything this web app stored? Its window must be closed.")
                                                  : qsTr("Remove this web app and delete its sign-in and data? Its window must be closed.")
                                            font.pixelSize: Look.small
                                        }
                                        TaosButton {
                                            objectName: "confirmButton"
                                            text: page.confirm.indexOf("clear:") === 0 ? qsTr("Clear data") : qsTr("Remove"); variant: "danger"; compact: true
                                            onClicked: {
                                                if (page.confirm.indexOf("clear:") === 0) page.web.clearData(inst.modelData.id)
                                                else { page.web.remove(inst.modelData.id); page.selected = "" }
                                                page.confirm = ""
                                            }
                                        }
                                        TaosButton { text: qsTr("Keep"); variant: "ghost"; compact: true; onClicked: page.confirm = "" }
                                    }
                                }
                            }
                        }
                    }
                }
                Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
            }

            // ---- the catalogue
            TaosText {
                Layout.leftMargin: 24; Layout.topMargin: 22; Layout.bottomMargin: 6
                text: page.mine.length > 0 ? qsTr("Add more") : qsTr("Available"); font.pixelSize: Look.small; font.weight: Font.DemiBold
                color: Look.textSecondary; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 24
                implicitHeight: Math.max(60, catCol.implicitHeight)
                radius: 10; color: Look.surface; clip: true
                ColumnLayout {
                    id: catCol
                    width: parent.width
                    spacing: 0
                    Repeater {
                        model: page.catalogue
                        delegate: AppRow {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            engine: page.engine
                            entry: modelData
                            last: index === page.catalogue.length - 1
                            objectName: "web-" + modelData.id
                            onOpen: (id) => page.openDetail(id)
                        }
                    }
                }
                TaosText {
                    visible: page.catalogue.length === 0
                    anchors.centerIn: parent
                    text: page.query !== "" ? qsTr("No web app matches “%1”. Use Add website for any address.").arg(page.query) : qsTr("Nothing here yet.")
                    color: Look.textSecondary
                }
                Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
            }
        }
    }
}
