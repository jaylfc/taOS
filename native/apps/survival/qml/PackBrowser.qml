// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Everything that can be downloaded, in one place: what is installed (with updates), the whole Kiwix library, the
// curated collections, maps by region, education channels and models. Each page says what a thing is, how big, what
// licence, and where it comes from.
ColumnLayout {
    id: root
    objectName: "packBrowser"
    property bool narrow: false
    property string tab: "installed"
    signal openRequested(string id)
    spacing: Look.space3

    Flickable {
        Layout.fillWidth: true
        Layout.preferredHeight: tabs.implicitHeight
        contentWidth: tabs.implicitWidth
        contentHeight: tabs.implicitHeight
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        Segmented {
            id: tabs
            accessibleName: qsTr("Where to get content")
            model: [{id: "installed", text: qsTr("Installed")}, {id: "browse", text: qsTr("Browse")}, {id: "collections", text: qsTr("Collections")},
                    {id: "maps", text: qsTr("Maps")}, {id: "learn", text: qsTr("Learn")}, {id: "models", text: qsTr("Models")}]
            currentId: root.tab
            onActivated: (id) => root.tab = id
        }
    }
    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        sourceComponent: root.tab === "browse" ? browse : root.tab === "collections" ? collections : root.tab === "maps" ? maps
                       : root.tab === "learn" ? learn : root.tab === "models" ? models : installed
    }
    Component { id: installed; InstalledPage { narrow: root.narrow; onOpenRequested: (id) => root.openRequested(id) } }
    Component { id: browse; BrowsePage { narrow: root.narrow } }
    Component { id: collections; CollectionsPage { narrow: root.narrow } }
    Component { id: maps; MapsPage { narrow: root.narrow } }
    Component { id: learn; LearnPage { narrow: root.narrow } }
    Component { id: models; ModelsPage { narrow: root.narrow } }
}
