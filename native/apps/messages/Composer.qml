import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The composer: multi-line, Enter sends, Shift+Enter inserts a newline, @mention
// autocomplete over the channel's members. The accent colour is used here for the
// send button and the focus ring, nowhere else in the transcript.
Item {
    id: root
    required property var engine
    property alias text: area.text
    property alias area: area
    implicitHeight: box.height + 16
    objectName: "composer"

    property var candidates: []
    property int candidateIndex: 0

    function focusInput() { area.forceActiveFocus() }
    function loadDraft() { area.text = engine.draft(engine.selectedId) }
    function updateMentions() {
        candidates = engine.mentionCandidates(area.text, area.cursorPosition)
        if (candidateIndex >= candidates.length) candidateIndex = 0
    }
    function accept(slug) {
        const cur = area.cursorPosition
        const t = engine.insertMention(area.text, cur, slug)
        const at = engine.mentionAtIndex(area.text, cur)
        area.text = t
        area.cursorPosition = at + slug.length + 2
        candidates = []
    }
    function submit() {
        if (area.text.trim() === "") return
        engine.send(area.text)
        area.text = ""
        candidates = []
    }

    Connections {
        target: root.engine
        function onSelectedChanged() { root.loadDraft(); root.focusInput() }
    }

    // mention popup, above the box
    Rectangle {
        id: pop
        objectName: "mentionPopup"
        visible: root.candidates.length > 0
        x: 16; width: Math.min(260, parent.width - 32)
        height: pl.count * 28 + 8
        y: -height - 2
        radius: 8; color: Look.raised; border.width: 1; border.color: Look.hairline
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Mention suggestions")
        ListView {
            id: pl
            anchors.fill: parent; anchors.margins: 4
            model: root.candidates
            interactive: false
            delegate: Rectangle {
                required property string modelData
                required property int index
                width: pl.width; height: 28; radius: 5
                color: index === root.candidateIndex ? Look.selection : "transparent"
                Accessible.role: Accessible.ListItem
                Accessible.name: "@" + modelData
                Accessible.selected: index === root.candidateIndex
                TaosText { anchors.verticalCenter: parent.verticalCenter; x: 8; text: "@" + modelData }
                MouseArea { anchors.fill: parent; onClicked: root.accept(modelData) }
            }
        }
    }

    Rectangle {
        id: box
        x: 12; y: 8
        width: parent.width - 24
        height: Math.max(44, Math.min(scroll.inputHeight, 140) + 20)
        radius: 10
        color: Look.field
        border.width: area.activeFocus ? 2 : 1
        border.color: area.activeFocus ? Look.focusRing : Look.hairline
        Behavior on height { NumberAnimation { duration: Look.fast } }

        C.ScrollView {
            id: scroll
            anchors { left: parent.left; right: sendBtn.left; top: parent.top; bottom: parent.bottom; margins: 1 }
            property real inputHeight: area.implicitHeight
            C.TextArea {
                id: area
                objectName: "composerInput"
                wrapMode: TextEdit.Wrap
                font.family: Look.family
                font.pixelSize: Look.body
                color: Look.text
                placeholderText: root.engine.selectedId === "" ? qsTr("Pick a conversation") : qsTr("Message %1").arg(root.engine.selectedName)
                placeholderTextColor: Look.textTertiary
                selectionColor: Look.selection
                selectedTextColor: Look.text
                leftPadding: 12; rightPadding: 8; topPadding: 10; bottomPadding: 10
                background: null
                enabled: root.engine.selectedId !== ""
                Accessible.name: qsTr("Message")
                Accessible.role: Accessible.EditableText
                onTextChanged: {
                    if (root.engine.selectedId !== "") {
                        root.engine.setDraft(root.engine.selectedId, text)
                        if (text !== "") root.engine.noteTyping()
                    }
                    root.updateMentions()
                }
                onCursorPositionChanged: root.updateMentions()
                Keys.onPressed: (e) => {
                    const popup = root.candidates.length > 0
                    if (popup && e.key === Qt.Key_Escape) { root.candidates = []; e.accepted = true; return }
                    if (popup && e.key === Qt.Key_Down) { root.candidateIndex = Math.min(root.candidates.length - 1, root.candidateIndex + 1); e.accepted = true; return }
                    if (popup && e.key === Qt.Key_Up) { root.candidateIndex = Math.max(0, root.candidateIndex - 1); e.accepted = true; return }
                    if (popup && (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Tab)) {
                        root.accept(root.candidates[root.candidateIndex]); e.accepted = true; return
                    }
                    if ((e.key === Qt.Key_Return || e.key === Qt.Key_Enter) && !(e.modifiers & Qt.ShiftModifier)) {
                        root.submit(); e.accepted = true
                    }
                }
            }
        }
        C.AbstractButton {
            id: sendBtn
            objectName: "sendButton"
            anchors { right: parent.right; bottom: parent.bottom; margins: 6 }
            width: 32; height: 32
            enabled: area.text.trim() !== "" && root.engine.selectedId !== ""
            focusPolicy: Qt.StrongFocus; hoverEnabled: true
            Accessible.role: Accessible.Button
            Accessible.name: qsTr("Send message")
            onClicked: { root.submit(); root.focusInput() }
            background: Rectangle {
                radius: 8
                color: sendBtn.enabled ? (sendBtn.hovered ? Qt.lighter(Look.accent, 1.1) : Look.accent) : Look.hover
                border.width: sendBtn.visualFocus ? 2 : 0; border.color: Look.focusRing
                Behavior on color { ColorAnimation { duration: Look.fast } }
            }
            contentItem: Glyph { anchors.centerIn: parent; name: "send"; size: 15
                color: sendBtn.enabled ? Look.accentText : Look.textTertiary }
        }
    }
}
