import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

// The frame every section page uses. A page declares its groups as children; `state` is "ready", "loading",
// "empty" or "error" (the content is replaced by the message, with Retry for an error). Deep links: reveal(id)
// scrolls the row with that settingId into view.
FocusScope {
    id: page
    property string sectionId: ""
    property string title: Settings.catalog.sectionTitle(sectionId)
    property string phase: "ready"
    property string message: ""
    property bool canRetry: true
    default property alias content: col.data
    signal retry()
    signal leave()
    Keys.onEscapePressed: page.leave()

    function find(item, id) {
        for (let i = 0; i < item.children.length; ++i) {
            const c = item.children[i]
            if (c.settingId === id && c.settingId !== undefined)
                return c
            const r = find(c, id)
            if (r) return r
        }
        return null
    }
    function reveal(id) {
        const it = find(col, id)
        if (!it) return
        const p = it.mapToItem(col, 0, 0)
        const fl = sv.contentItem
        fl.contentY = Math.max(0, Math.min(fl.contentHeight - fl.height, p.y - 24))
    }
    Connections {
        target: Settings.nav
        function onRevealRequested(id) { page.reveal(id) }
    }
    Component.onCompleted: if (Settings.nav.setting.length) revealLater.start()
    Timer { id: revealLater; interval: 0; onTriggered: page.reveal(Settings.nav.setting) }

    C.ScrollView {
        id: sv
        anchors.fill: parent
        contentWidth: availableWidth
        visible: page.phase === "ready"
        clip: true
        Column {
            id: col
            x: Math.max(24, (sv.availableWidth - width) / 2)
            y: 0
            width: Math.min(sv.availableWidth - 48, 680)
            topPadding: 24
            bottomPadding: 32
            spacing: 24
            Txt {
                width: parent.width
                text: page.title
                font.pixelSize: TaosStyle.titleSize + 6
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
        }
    }

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 420)
        visible: page.phase !== "ready"
        spacing: 12
        Accessible.role: page.phase === "error" ? Accessible.AlertMessage : Accessible.StaticText
        Accessible.name: page.message
        Txt {
            Layout.fillWidth: true
            text: page.phase === "loading" ? qsTr("Loading...") : page.message
            horizontalAlignment: Text.AlignHCenter
            color: page.phase === "error" ? TaosStyle.danger : TaosStyle.textSecondary
        }
        Btn {
            Layout.alignment: Qt.AlignHCenter
            visible: page.phase === "error" && page.canRetry
            text: qsTr("Try again")
            onClicked: page.retry()
        }
    }
    onPhaseChanged: if (phase !== "ready") Settings.announce(page, message.length ? message : qsTr("Loading"))
}
