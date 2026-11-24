// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import "qrc:/taos/qml/Taos/Apps/Logic.js" as Logic

// One notification: tone glyph, title and body, relative time and source, a
// dismiss button, and (for consent and sign-in rows) the shared action block.
Item {
    id: root
    required property var model
    required property var store
    required property int index
    property alias headerItem: header

    readonly property bool unread: !model.read
    readonly property bool pinned: model.pinned === true || model.source === "auth_requests" || model.source === "agent_scope_requests"
    readonly property var rowData: model.data || null
    readonly property var consent: pinned ? Logic.consentPayload(rowData) : null
    readonly property var signIn: model.source === "auth_broker" ? Logic.signInPayload(rowData) : null
    readonly property string levelName: ["success", "warning", "error"].indexOf(model.level) >= 0 ? model.level : "info"
    readonly property bool hasTarget: store.targetFor(model.id).valid === true
    signal focusNeighbour(int delta)

    width: parent ? parent.width : 400
    height: card.height + 8

    function focusHeader() { header.forceActiveFocus(Qt.TabFocusReason) }

    GlassPanel {
        id: card
        x: 12
        width: parent.width - 24
        height: body.implicitHeight + 24
        radius: 12
        color: root.unread ? Look.cardUnread : Look.card

        ColumnLayout {
            id: body
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                // The tap target: tone glyph, text, meta. One accessible button.
                Item {
                    id: header
                    objectName: "rowHeader"
                    Layout.fillWidth: true
                    implicitHeight: textCol.implicitHeight
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: root.model.title + ". " + root.model.message + ". "
                        + Format.relativeTime(root.model.timestamp, Format.tick) + ", " + root.model.source
                        + (root.unread ? ", unread" : "")
                    Accessible.description: root.hasTarget ? "Opens the related app" : ""
                    Accessible.onPressAction: root.store.activate(root.model.id)
                    Keys.onReturnPressed: root.store.activate(root.model.id)
                    Keys.onEnterPressed: root.store.activate(root.model.id)
                    Keys.onSpacePressed: root.store.activate(root.model.id)
                    Keys.onDownPressed: root.focusNeighbour(1)
                    Keys.onUpPressed: root.focusNeighbour(-1)
                    Keys.onDeletePressed: root.store.dismiss(root.model.id)
                    Keys.onPressed: (e) => {
                        if (e.key === Qt.Key_M && !(e.modifiers & ~Qt.ShiftModifier)) {
                            root.store.markRead(root.model.id); e.accepted = true
                        }
                    }

                    Rectangle {                       // focus ring
                        anchors.fill: parent; anchors.margins: -4
                        radius: 10; color: "transparent"
                        border.width: 2; border.color: Look.focusRing
                        visible: header.activeFocus
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { header.forceActiveFocus(Qt.MouseFocusReason); root.store.activate(root.model.id) }
                    }
                    RowLayout {
                        anchors.fill: parent
                        spacing: 10
                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            width: 30; height: 30; radius: 15
                            color: Look.tint(Look.tone(root.levelName), 0.16)
                            Glyph {
                                anchors.centerIn: parent; size: 15
                                name: root.pinned ? "shield" : root.levelName === "success" ? "check"
                                    : root.levelName === "warning" || root.levelName === "error" ? "alert" : "info"
                                color: Look.tone(root.levelName)
                            }
                        }
                        ColumnLayout {
                            id: textCol
                            Layout.fillWidth: true
                            spacing: 3
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Rectangle { visible: root.unread; width: 7; height: 7; radius: 3.5; color: Look.live
                                            Layout.alignment: Qt.AlignVCenter }
                                TaosText {
                                    Layout.fillWidth: true
                                    text: root.model.title
                                    elide: Text.ElideRight
                                    font.pixelSize: Look.bodyLarge
                                    font.weight: root.unread ? Font.DemiBold : Font.Medium
                                    color: root.unread ? Look.text : Look.textSecondary
                                }
                            }
                            TaosText {
                                visible: root.model.message !== ""
                                Layout.fillWidth: true
                                text: root.model.message
                                wrapMode: Text.Wrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                                color: Look.textSecondary
                            }
                            RowLayout {
                                spacing: 6
                                TaosText { text: Format.relativeTime(root.model.timestamp, Format.tick)
                                           font.pixelSize: Look.caption; color: Look.textTertiary }
                                TaosText { text: "·"; font.pixelSize: Look.caption; color: Look.textTertiary }
                                TaosText { text: root.model.source; font.pixelSize: Look.caption; color: Look.textTertiary }
                                Glyph { visible: root.hasTarget; name: "chevron-right"; size: 11; stroke: 2; color: Look.textTertiary }
                            }
                        }
                    }
                }

                TaosButton {
                    Layout.alignment: Qt.AlignTop
                    variant: "ghost"
                    compact: true
                    glyph: "x"
                    accessibleName: "Dismiss: " + root.model.title
                    onClicked: root.store.dismiss(root.model.id)
                }
            }

            Loader {
                id: consentLoader
                active: root.consent !== null
                visible: active
                Layout.fillWidth: true
                sourceComponent: ConsentCard {
                    width: consentLoader.width
                    requestId: root.consent.requestId
                    scopes: root.consent.scopes
                    requestedProjectId: root.consent.projectId
                    source: root.model.source
                    canonicalId: root.consent.canonicalId
                    humanDuration: root.consent.humanDuration
                    harness: root.consent.harness
                    agentLabel: root.consent.agent
                    onResolved: root.store.resolved(root.model.id)
                    onRefetchRequested: root.store.refresh()
                }
            }
            Loader {
                id: signInLoader
                active: root.signIn !== null
                visible: active
                Layout.fillWidth: true
                sourceComponent: SignInCard {
                    width: signInLoader.width
                    requestId: root.signIn.requestId
                    url: root.signIn.url
                    host: root.signIn.host
                    agent: root.signIn.agent
                    onResolved: root.store.resolved(root.model.id)
                }
            }
        }
    }
}
