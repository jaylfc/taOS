import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0

// A glass card with a title, a trailing summary and content. On a phone the
// card collapses to its header row (`collapsible`), toggled by tap or Enter.
Item {
    id: card
    property string title: ""
    property string summary: ""
    property bool collapsible: false
    property bool open: false          // view state: expanded on a phone
    property alias trailing: trailingSlot.data
    default property alias content: body.data
    readonly property bool shown: !collapsible || open

    Layout.fillWidth: true
    Layout.fillHeight: true
    implicitHeight: panel.implicitHeight
    implicitWidth: 300

    Glass { id: panel; anchors.fill: parent; implicitHeight: col.implicitHeight + 2 * Pal.pad }

    ColumnLayout {
        id: col
        x: Pal.pad; y: Pal.pad
        width: card.width - 2 * Pal.pad
        spacing: Pal.gap

        Item {
            id: head
            Layout.fillWidth: true
            implicitHeight: 24
            activeFocusOnTab: card.collapsible
            Accessible.role: card.collapsible ? Accessible.Button : Accessible.Heading
            Accessible.name: card.title
            Accessible.description: card.summary
            Accessible.onPressAction: if (card.collapsible) card.open = !card.open
            Keys.onReturnPressed: if (card.collapsible) card.open = !card.open
            Keys.onSpacePressed: if (card.collapsible) card.open = !card.open
            Rectangle {   // keyboard focus ring
                anchors.fill: parent; anchors.margins: -4; radius: 8
                color: "transparent"; border.width: 2; border.color: Pal.focusRing
                visible: head.activeFocus
            }
            RowLayout {
                anchors.fill: parent
                spacing: 8
                Txt { text: card.title; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignVCenter }
                Item { Layout.fillWidth: true }
                Txt {
                    text: card.summary
                    visible: text.length > 0
                    font.pixelSize: 12
                    color: TaosStyle.textSecondary
                    Layout.maximumWidth: card.width * 0.55
                }
                Item { id: trailingSlot; implicitWidth: childrenRect.width; implicitHeight: childrenRect.height; visible: children.length > 0 }
                Txt {
                    visible: card.collapsible
                    text: card.open ? "⌃" : "⌄"
                    color: TaosStyle.textTertiary
                    font.pixelSize: 13
                }
            }
            MouseArea { anchors.fill: parent; enabled: card.collapsible; onClicked: card.open = !card.open; z: -1 }
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Pal.gap
            visible: card.shown
        }
    }
}
