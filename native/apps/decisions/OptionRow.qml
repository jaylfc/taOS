import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// One answer option: a toggle row (radio for pick-one, checkbox for pick-any).
Rectangle {
    id: root
    property string label: ""
    property string rationale: ""
    property bool recommended: false
    property bool selected: false
    property bool multi: false
    property bool busy: false
    signal toggled()

    implicitHeight: content.implicitHeight + 18
    radius: 10
    color: selected ? Look.tint(Look.accent, 0.16) : (hover.hovered ? Look.hover : Look.field)
    border.width: root.activeFocus ? 2 : 1
    border.color: root.activeFocus ? Look.accent : selected ? Look.tint(Look.accent, 0.55) : Look.hairline
    opacity: busy ? 0.6 : 1
    Behavior on color { ColorAnimation { duration: Look.fast } }

    activeFocusOnTab: true
    Accessible.role: multi ? Accessible.CheckBox : Accessible.RadioButton
    Accessible.checkable: true
    Accessible.checked: selected
    Accessible.name: label + (recommended ? ", recommended" : "") + (rationale !== "" ? ". " + rationale : "")
    Accessible.onToggleAction: if (!busy) toggled()
    Accessible.onPressAction: if (!busy) toggled()
    Keys.onSpacePressed: if (!busy) toggled()
    Keys.onReturnPressed: if (!busy) toggled()

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: { root.forceActiveFocus(Qt.MouseFocusReason); if (!root.busy) root.toggled() } }

    RowLayout {
        id: content
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
        spacing: 10
        Rectangle {                                   // mark
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 1
            width: 18; height: 18
            radius: root.multi ? 5 : 9
            color: root.selected ? Look.accent : "transparent"
            border.width: 1.5
            border.color: root.selected ? Look.accent : Look.glassStroke
            Behavior on color { ColorAnimation { duration: Look.fast } }
            Glyph { anchors.centerIn: parent; name: "check"; size: 12; stroke: 2.6; color: Look.onAccent
                    opacity: root.selected ? 1 : 0; scale: root.selected ? 1 : 0.6
                    Behavior on opacity { NumberAnimation { duration: Look.fast } }
                    Behavior on scale { NumberAnimation { duration: Look.fast; easing.type: Easing.OutBack } } }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            RowLayout {
                spacing: 8
                TaosText { text: root.label; font.pixelSize: Look.bodyLarge; font.weight: Font.Medium
                           Layout.fillWidth: true; wrapMode: Text.Wrap }
                Chip { visible: root.recommended; text: qsTr("Recommended"); tone: "accent"; glyph: "star" }
            }
            TaosText { visible: root.rationale !== ""; text: root.rationale; color: Look.textSecondary
                       font.pixelSize: Look.small; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
    }
}
