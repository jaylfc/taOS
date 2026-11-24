// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Apps 1.0

// A multi-line field with a fixed height (the answers it holds are bounded).
Item {
    id: root
    property alias text: area.text
    property alias placeholderText: area.placeholderText
    property int maximumLength: 0
    property string accessibleName: placeholderText
    property int rows: 3
    signal edited()
    implicitHeight: rows * (Look.body + 6) + 16
    implicitWidth: 200
    function forceFocus() { area.forceActiveFocus() }
    Rectangle {
        anchors.fill: parent
        radius: Look.radiusControl
        color: Look.field
        border.width: area.activeFocus ? 2 : 1
        border.color: area.activeFocus ? Look.focusRing : Look.hairline
        Behavior on border.color { ColorAnimation { duration: Look.fast } }
    }
    C.ScrollView {
        anchors.fill: parent
        anchors.margins: 1
        C.TextArea {
            id: area
            wrapMode: TextEdit.Wrap
            font.family: Look.family
            font.pixelSize: Look.body
            color: Look.text
            placeholderTextColor: Look.textTertiary
            selectionColor: Look.selection
            selectedTextColor: Look.text
            leftPadding: 9; rightPadding: 9; topPadding: 7; bottomPadding: 7
            background: null
            Accessible.name: root.accessibleName
            Accessible.role: Accessible.EditableText
            onTextChanged: {
                // a TextArea has no maximumLength: keep to the web app's limits by hand
                if (root.maximumLength > 0 && text.length > root.maximumLength) {
                    text = text.substring(0, root.maximumLength)
                    return
                }
                root.edited()
            }
            // Tab moves on; it is not a character of the answer.
            KeyNavigation.priority: KeyNavigation.BeforeItem
            Keys.onTabPressed: (e) => { nextItemInFocusChain(true).forceActiveFocus(); e.accepted = true }
            Keys.onBacktabPressed: (e) => { nextItemInFocusChain(false).forceActiveFocus(); e.accepted = true }
        }
    }
}
