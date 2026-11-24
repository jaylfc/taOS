import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One message in the transcript: a plain, dense row (no bubble). The first row of a
// run shows the author and time; later rows of the run show the time on hover.
Item {
    id: root
    required property var model
    required property var engine
    required property int index
    width: ListView.view ? ListView.view.width : 600
    implicitHeight: col.implicitHeight
    height: implicitHeight
    objectName: "messageRow"

    readonly property bool own: model.own
    readonly property bool deleted: model.deleted
    readonly property bool failed: model.state === "failed"
    readonly property bool sending: model.state === "sending"
    readonly property bool collapsedLong: model.isLong && !model.expanded
    readonly property string summary: {
        let t = model.content
        return t.length > 400 ? t.substring(0, 400) + "..." : t
    }

    HoverHandler { id: hover }

    Accessible.role: Accessible.ListItem
    Accessible.name: model.authorName + ", " + model.fullTime + ". "
        + (deleted ? qsTr("Message deleted") : summary)
        + (failed ? qsTr(". Not sent") : sending ? qsTr(". Sending") : "")

    Column {
        id: col
        width: parent.width
        spacing: 0

        // day separator
        Item {
            visible: model.dayLabel !== ""
            width: parent.width; height: visible ? 34 : 0
            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 16; width: parent.width - 32; height: 1; color: Look.hairline }
            Rectangle {
                anchors.centerIn: parent
                width: dayText.implicitWidth + 16; height: 20; radius: 10
                color: Look.ground; border.width: 1; border.color: Look.hairline
                TaosText { id: dayText; anchors.centerIn: parent; text: model.dayLabel; font.pixelSize: Look.caption
                           font.weight: Font.DemiBold; color: Look.textSecondary
                           Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text }
            }
        }
        // first unread
        Item {
            visible: model.newDivider
            width: parent.width; height: visible ? 22 : 0
            objectName: "newDivider"
            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 16; width: parent.width - 32; height: 1; color: Look.text; opacity: 0.35 }
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter; anchors.right: parent.right; anchors.rightMargin: 16
                width: nt.implicitWidth + 12; height: 16; radius: 8; color: Look.text
                TaosText { id: nt; anchors.centerIn: parent; text: qsTr("New"); font.pixelSize: Look.caption
                           font.weight: Font.DemiBold; color: Look.ground
                           Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: qsTr("New messages from here") }
            }
        }

        Rectangle {
            width: parent.width
            height: body.implicitHeight + (model.groupStart ? 10 : 2) + 2
            color: hover.hovered ? Look.hover : "transparent"
            Behavior on color { ColorAnimation { duration: Look.fast } }

            // own messages: a thin rule on the left, nothing else
            Rectangle { visible: root.own; x: 0; y: model.groupStart ? 8 : 0; width: 2; height: parent.height - (model.groupStart ? 8 : 0); color: Look.hairline }

            // gutter: avatar initial at the head of a run, the time on hover otherwise
            Item {
                x: 16; y: model.groupStart ? 10 : 2; width: 36; height: 22
                Rectangle {
                    visible: model.groupStart
                    width: 28; height: 28; radius: 7; y: 0
                    color: Look.field; border.width: 1; border.color: Look.hairline
                    TaosText { anchors.centerIn: parent; text: (model.authorName || "?").charAt(0).toUpperCase()
                               font.weight: Font.DemiBold; color: Look.textSecondary }
                }
                TaosText {
                    visible: !model.groupStart && hover.hovered
                    anchors.verticalCenter: parent.verticalCenter
                    text: model.timeText; font.pixelSize: Look.caption; font.features: { "tnum": 1 }; color: Look.textTertiary
                }
            }

            ColumnLayout {
                id: body
                x: 60; y: model.groupStart ? 8 : 0
                width: parent.width - 60 - 20
                spacing: 2

                RowLayout {
                    visible: model.groupStart
                    spacing: 8
                    TaosText {
                        text: model.authorName
                        font.weight: Font.DemiBold
                        color: root.own ? Look.textSecondary : Look.text
                    }
                    TaosText {
                        visible: model.authorType === "agent"
                        text: qsTr("agent"); font.pixelSize: Look.caption; color: Look.textTertiary
                    }
                    TaosText {
                        text: model.timeText; font.pixelSize: Look.small; color: Look.textTertiary; font.features: { "tnum": 1 }
                        Accessible.ignored: true
                    }
                    TaosText { visible: model.edited; text: qsTr("(edited)"); font.pixelSize: Look.small; color: Look.textTertiary }
                }

                // tombstone
                TaosText {
                    visible: root.deleted
                    text: qsTr("Message deleted"); font.italic: true; color: Look.textTertiary
                }

                // the body, part by part
                Item {
                    id: bodyClip
                    visible: !root.deleted
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(parts.implicitHeight, root.collapsedLong ? 340 : parts.implicitHeight)
                    clip: root.collapsedLong
                    Column {
                        id: parts
                        width: parent.width
                        spacing: 6
                        Repeater {
                            model: root.model.parts
                            delegate: Loader {
                                required property var modelData
                                width: parts.width
                                sourceComponent: modelData.kind === "html" ? htmlC
                                    : modelData.kind === "code" ? codeC
                                    : modelData.kind === "thinking" ? thinkC
                                    : modelData.kind === "tool" ? toolC
                                    : modelData.kind === "status" ? statusC
                                    : modelData.kind === "decision" ? decisionC : unknownC
                                property var part: modelData
                            }
                        }
                    }
                    // fade hint at the clip edge
                    Rectangle {
                        visible: root.collapsedLong
                        anchors.bottom: parent.bottom; width: parent.width; height: 28
                        gradient: Gradient {
                            GradientStop { position: 0; color: "transparent" }
                            GradientStop { position: 1; color: Look.ground }
                        }
                    }
                }
                C.AbstractButton {
                    id: moreBtn
                    visible: root.model.isLong && !root.deleted
                    objectName: "showMore"
                    implicitHeight: 22; implicitWidth: moreText.implicitWidth + 12
                    focusPolicy: Qt.StrongFocus; hoverEnabled: true
                    Accessible.role: Accessible.Button
                    Accessible.name: root.model.expanded ? qsTr("Show less") : qsTr("Show more")
                    onClicked: root.engine.messages.toggleExpanded(root.model.msgId)
                    background: Rectangle { radius: 5; color: moreBtn.hovered ? Look.hover : "transparent"
                        border.width: moreBtn.visualFocus ? 2 : 0; border.color: Look.focusRing }
                    contentItem: TaosText { id: moreText; text: root.model.expanded ? qsTr("Show less") : qsTr("Show more")
                        font.pixelSize: Look.small; font.weight: Font.Medium; color: Look.textSecondary }
                }
                // streaming / pending
                TaosText {
                    visible: !root.deleted && (root.model.state === "streaming" || root.model.state === "pending")
                    text: root.model.parts.length === 0 ? qsTr("Working...") : qsTr("Typing...")
                    color: Look.textTertiary; font.pixelSize: Look.small
                    Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
                }
                TaosText {
                    visible: !root.deleted && root.model.state === "error"
                    text: qsTr("The reply stopped with an error."); color: Look.danger; font.pixelSize: Look.small
                    Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
                }
                // reactions
                TaosText {
                    visible: root.model.reactionsText !== "" && !root.deleted
                    text: root.model.reactionsText; font.pixelSize: Look.small; color: Look.textSecondary
                }
                TaosText {
                    visible: root.model.replyCount > 0
                    text: qsTr("%n replies", "", root.model.replyCount); font.pixelSize: Look.small; color: Look.textSecondary
                }
                // own message state
                RowLayout {
                    visible: root.sending || root.failed
                    spacing: 8
                    TaosText {
                        text: root.sending ? qsTr("Sending...") : qsTr("Not sent. %1").arg(root.model.errorText)
                        color: root.failed ? Look.danger : Look.textTertiary; font.pixelSize: Look.small
                        Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
                    }
                    TaosButton { visible: root.failed; text: qsTr("Retry"); objectName: "retrySend"
                                 onClicked: root.engine.retry(root.model.msgId) }
                    TaosButton { visible: root.failed; text: qsTr("Discard"); objectName: "discardSend"
                                 onClicked: root.engine.discard(root.model.msgId) }
                }
            }

            // copy whole message
            C.AbstractButton {
                id: copyBtn
                visible: hover.hovered && !root.deleted && !root.sending
                x: parent.width - 32; y: model.groupStart ? 6 : 0
                width: 24; height: 24
                focusPolicy: Qt.StrongFocus; hoverEnabled: true
                Accessible.role: Accessible.Button; Accessible.name: qsTr("Copy message text")
                onClicked: root.engine.copyText(root.model.content)
                background: Rectangle { radius: 5; color: copyBtn.hovered ? Look.raised : "transparent"
                    border.width: copyBtn.visualFocus ? 2 : 0; border.color: Look.focusRing }
                contentItem: MIcon { anchors.centerIn: parent; name: "copy"; size: 14; color: Look.textSecondary }
            }
        }
    }

    Component {
        id: htmlC
        TaosText {
            property var part: parent ? parent.part : null
            text: part ? "<style>a { color: " + Look.text + "; } td { padding: 2px 8px; }</style>" + part.html : ""
            textFormat: Text.RichText
            wrapMode: Text.Wrap
            font.pixelSize: Look.body
            lineHeight: 1.25
            Accessible.ignored: true
            onLinkActivated: (l) => root.engine.openLink(l)
            HoverHandler { cursorShape: parent.hoveredLink !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor }
        }
    }
    Component {
        id: codeC
        Rectangle {
            id: cb
            property var part: parent ? parent.part : null
            width: parent ? parent.width : 100
            implicitHeight: codeText.implicitHeight + 20
            height: implicitHeight
            radius: 7; color: Look.field; border.width: 1; border.color: Look.hairline
            Flickable {
                anchors.fill: parent; anchors.margins: 10
                contentWidth: codeText.implicitWidth; clip: true
                interactive: contentWidth > width
                TaosText {
                    id: codeText
                    text: cb.part ? cb.part.text : ""
                    font.family: "monospace"; font.pixelSize: Look.small
                    Accessible.ignored: true
                }
            }
            C.AbstractButton {
                id: ccb
                visible: hover.hovered
                anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 4
                width: 24; height: 24
                focusPolicy: Qt.StrongFocus; hoverEnabled: true
                Accessible.role: Accessible.Button; Accessible.name: qsTr("Copy code")
                onClicked: root.engine.copyText(cb.part.text)
                background: Rectangle { radius: 5; color: ccb.hovered ? Look.raised : Look.field
                    border.width: ccb.visualFocus ? 2 : 1; border.color: ccb.visualFocus ? Look.focusRing : Look.hairline }
                contentItem: MIcon { anchors.centerIn: parent; name: "copy"; size: 13 }
            }
        }
    }
    Component {
        id: thinkC
        Column {
            id: tk
            property var part: parent ? parent.part : null
            property bool open: part ? part.open === true : false
            width: parent ? parent.width : 100
            spacing: 4
            C.AbstractButton {
                id: tb
                implicitHeight: 22; implicitWidth: tt.implicitWidth + 28
                focusPolicy: Qt.StrongFocus; hoverEnabled: true
                Accessible.role: Accessible.Button
                Accessible.name: tk.open ? qsTr("Collapse thinking") : qsTr("Expand thinking")
                Accessible.checked: tk.open
                onClicked: tk.open = !tk.open
                background: Rectangle { radius: 5; color: tb.hovered ? Look.hover : "transparent"
                    border.width: tb.visualFocus ? 2 : 0; border.color: Look.focusRing }
                contentItem: Row {
                    spacing: 6
                    Glyph { anchors.verticalCenter: parent.verticalCenter; name: tk.open ? "chevron-down" : "chevron-right"; size: 12; color: Look.textTertiary }
                    TaosText { id: tt; anchors.verticalCenter: parent.verticalCenter; text: qsTr("Thinking"); font.pixelSize: Look.small
                               font.weight: Font.DemiBold; color: Look.textTertiary }
                }
            }
            TaosText {
                visible: tk.open
                width: parent.width
                text: tk.part ? tk.part.html : ""
                textFormat: Text.RichText; wrapMode: Text.Wrap; color: Look.textTertiary
                Accessible.ignored: true
            }
        }
    }
    Component {
        id: toolC
        Rectangle {
            id: tc
            property var part: parent ? parent.part : null
            width: parent ? parent.width : 100
            implicitHeight: tcol.implicitHeight + 14
            height: implicitHeight
            radius: 7; color: Look.field; border.width: 1; border.color: Look.hairline
            Accessible.role: Accessible.StaticText
            Accessible.name: part ? qsTr("Tool %1, %2").arg(part.name).arg(part.status) : ""
            Column {
                id: tcol
                x: 10; y: 7; width: parent.width - 20
                spacing: 2
                Row {
                    spacing: 8
                    TaosText { text: tc.part ? tc.part.name : ""; font.family: "monospace"; font.pixelSize: Look.small; font.weight: Font.DemiBold }
                    TaosText { text: tc.part ? tc.part.status : ""; font.pixelSize: Look.small
                               color: tc.part && tc.part.status === "error" ? Look.danger : tc.part && tc.part.status === "done" ? Look.success : Look.textTertiary }
                }
                TaosText { visible: tc.part && tc.part.input !== ""; width: parent.width; text: tc.part ? tc.part.input : ""
                           elide: Text.ElideRight; font.family: "monospace"; font.pixelSize: Look.caption; color: Look.textTertiary }
                TaosText { visible: tc.part && tc.part.result !== ""; width: parent.width; text: tc.part ? tc.part.result : ""
                           elide: Text.ElideRight; maximumLineCount: 3; wrapMode: Text.Wrap; font.family: "monospace"; font.pixelSize: Look.caption; color: Look.textSecondary }
            }
        }
    }
    Component {
        id: statusC
        Column {
            id: sc
            property var part: parent ? parent.part : null
            width: parent ? parent.width : 100
            spacing: 4
            TaosText { width: parent.width; text: sc.part ? sc.part.text : ""; wrapMode: Text.Wrap; color: Look.textSecondary
                       font.italic: !(sc.part && sc.part.question); Accessible.ignored: true }
            Flow {
                width: parent.width; spacing: 6
                Repeater {
                    model: sc.part ? sc.part.options : []
                    delegate: Rectangle {
                        required property string modelData
                        implicitWidth: ot.implicitWidth + 16; implicitHeight: 22; radius: 11
                        color: "transparent"; border.width: 1; border.color: Look.hairline
                        TaosText { id: ot; anchors.centerIn: parent; text: modelData; font.pixelSize: Look.small; color: Look.textSecondary }
                    }
                }
            }
        }
    }
    Component {
        id: decisionC
        TaosText { text: qsTr("A decision was requested. Answer it in Decisions."); color: Look.textSecondary; font.pixelSize: Look.small
                   wrapMode: Text.Wrap; width: parent ? parent.width : 100 }
    }
    Component {
        id: unknownC
        TaosText { property var part: parent ? parent.part : null
                   text: qsTr("unsupported block: %1").arg(part ? part.name : ""); color: Look.textTertiary; font.pixelSize: Look.small }
    }
}
