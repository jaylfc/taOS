// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import Taos 1.0
import Taos.Apps 1.0

// The same sign-in as SignInForm, as a small sheet an app opens from its own "Sign in" button while the rest of
// the app stays visible (the Store keeps showing its saved catalogue). Closes itself when the session is signed in.
C.Popup {
    id: root
    property string appName: ""
    modal: true
    focus: true
    padding: 0
    width: 340
    anchors.centerIn: C.Overlay.overlay
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    background: Item {}
    contentItem: SignInForm { appName: root.appName }
    Connections {
        target: Taos.session
        function onSignedIn() { root.close() }
    }
}
