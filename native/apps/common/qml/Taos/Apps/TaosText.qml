// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// Text in the taOS face and colours. Decorative by default for assistive tech
// (the parent control carries the accessible name).
Text {
    font.family: Look.family
    font.pixelSize: Look.body
    color: Look.text
    // Plain text unless a caller asks otherwise: this component shows strings that apps, agents and
    // other people supply, and Qt's automatic detection would render markup in them (even fetch an <img>).
    textFormat: Text.PlainText
    elide: Text.ElideNone
    wrapMode: Text.NoWrap
    renderType: Text.NativeRendering
    Accessible.ignored: true
}
