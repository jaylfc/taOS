// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The reader. The article arrives as the small HTML subset the engine rebuilt (no scripts, no remote loads) and is
// shown with Qt's own rich text: no web view. The style sheet is built from the theme tokens, so a theme change
// restyles the page at once.
Item {
    id: root
    property bool narrow: false
    signal backToList()
    readonly property var lib: Survival.library
    readonly property var art: lib.article
    readonly property int bodyPx: 16

    function css() {
        const t = String(Look.text), s = String(Look.textSecondary), h = String(Look.hairline)
        return "<style>"
            + "body { color: " + t + "; font-size: " + bodyPx + "px; }"
            + "a { color: " + String(Look.accent) + "; }"
            + "h1 { font-size: 30px; font-weight: 700; margin-top: 4px; margin-bottom: 10px; }"
            + "h2 { font-size: 23px; font-weight: 600; margin-top: 22px; margin-bottom: 8px; }"
            + "h3 { font-size: 19px; font-weight: 600; margin-top: 18px; margin-bottom: 6px; }"
            + "h4, h5, h6 { font-size: 16px; font-weight: 600; margin-top: 14px; margin-bottom: 4px; }"
            + "p { margin-top: 0px; margin-bottom: 10px; }"
            + "li { margin-bottom: 4px; }"
            + "th { font-weight: 600; }"
            + "td, th { font-size: " + (bodyPx - 2) + "px; }"
            + "blockquote { margin-left: 18px; color: " + s + "; }"
            + "pre, code, tt { font-family: monospace; }"
            + "small, sub, sup { font-size: " + (bodyPx - 3) + "px; }"
            + "</style>"
    }
    function wrapHtml(body) {
        return "<html><head>" + css() + "</head><body>" + body + "</body></html>"
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        // toolbar
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3
            spacing: Look.space1
            TaosButton { visible: root.narrow; glyph: "chevron-left"; text: qsTr("Results"); variant: "ghost"; onClicked: root.backToList() }
            TaosButton { glyph: "arrow-left"; compact: false; variant: "ghost"; enabled: root.lib.canBack; accessibleName: qsTr("Back")
                         onClicked: root.lib.back() }
            TaosButton { glyph: "chevron-right"; variant: "ghost"; enabled: root.lib.canForward; accessibleName: qsTr("Forward")
                         onClicked: root.lib.forward() }
            TaosButton { glyph: "home"; variant: "ghost"; accessibleName: qsTr("Start page"); onClicked: root.lib.home() }
            TaosButton { glyph: "shuffle"; variant: "ghost"; accessibleName: qsTr("A random article"); onClicked: root.lib.random() }
            TaosText {
                Layout.fillWidth: true
                Layout.leftMargin: Look.space2
                text: root.art.title || ""
                elide: Text.ElideRight
                font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; color: Look.textSecondary
                horizontalAlignment: root.narrow ? Text.AlignRight : Text.AlignLeft
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Flickable {
                id: flick
                visible: root.art.kind === "article" || root.art.kind === "image"
                anchors.fill: parent
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: width
                contentHeight: pageItem.implicitHeight
                C.ScrollBar.vertical: C.ScrollBar { }
                // a new page starts at the top
                Connections { target: root.lib; function onArticleChanged() { flick.contentY = 0 } }
                Item {
                    id: pageItem
                    width: flick.width
                    implicitHeight: pageCol.implicitHeight + Look.space6 * 2
                    ColumnLayout {
                        id: pageCol
                        width: Math.min(760, flick.width - (root.narrow ? Look.space4 * 2 : Look.space6 * 2))
                        x: (parent.width - width) / 2
                        y: Look.space5
                        spacing: Look.space3
                        TaosText {
                            visible: root.art.kind === "article" && (root.art.blocked || 0) > 0
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            font.pixelSize: Look.small; color: Look.textTertiary
                            text: qsTr("Links and pictures that point to the internet are switched off here. This library works offline.")
                        }
                        Text {
                            id: bodyText
                            visible: root.art.kind === "article"
                            Layout.fillWidth: true
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            text: root.art.kind === "article" ? root.wrapHtml(root.art.html) : ""
                            font.family: Look.family
                            font.pixelSize: root.bodyPx
                            color: Look.text
                            linkColor: Look.accent
                            lineHeight: 1.42
                            renderType: Text.NativeRendering
                            onLinkActivated: (link) => root.lib.openLink(link)
                            Accessible.role: Accessible.StaticText
                            Accessible.name: root.art.title || ""
                            HoverHandler { cursorShape: bodyText.hoveredLink !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor }
                        }
                        Image {
                            visible: root.art.kind === "image"
                            source: root.art.kind === "image" ? root.art.imageUrl : ""
                            fillMode: Image.PreserveAspectFit
                            Layout.fillWidth: true
                            Layout.preferredHeight: implicitHeight > 0 ? implicitHeight * Math.min(1, width / Math.max(1, implicitWidth)) : 0
                        }
                        TaosText {
                            visible: root.art.kind === "article" && root.art.truncated === true
                            Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.warning
                            text: qsTr("This article is very long and was cut short here.")
                        }
                        // the licence line every page of the pack carries
                        TaosText {
                            visible: root.art.kind === "article" && !!root.lib.info.attribution
                            Layout.fillWidth: true; Layout.topMargin: Look.space4; wrapMode: Text.Wrap
                            font.pixelSize: Look.small; color: Look.textTertiary
                            text: root.lib.info.attribution || ""
                        }
                    }
                }
            }
            EmptyState {
                visible: root.art.kind === "missing"
                anchors.centerIn: parent
                glyph: "search"; title: qsTr("That page is not in this pack"); detail: qsTr("The link points to something the pack does not hold. Try a search instead.")
            }
            EmptyState {
                visible: root.art.kind === "unsupported"
                anchors.centerIn: parent
                glyph: "info"
                title: qsTr("This entry cannot be shown yet")
                detail: qsTr("It is a %1 file (%2). Survival Studio shows text and pictures from a pack; documents such as PDFs are not supported yet.").arg(root.art.mime || "").arg(root.art.sizeText || "")
            }
            EmptyState {
                visible: !root.art.kind
                anchors.centerIn: parent
                glyph: "book"; title: qsTr("Pick something to read"); detail: qsTr("Search the pack, or open its start page or a random article.")
            }
        }
    }
}
