// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The faint line texture some themes put on shell surfaces (skin texture "scanlines"): a 1 px line every 3 px,
// the text colour at 3 percent, drawn over the surface and under the content. Nothing is drawn for other skins.
Item {
    id: sc
    visible: Theme.scanlines
    clip: true
    Repeater {
        model: sc.visible ? Math.ceil(sc.height / 3) : 0
        Rectangle {
            y: index * 3
            width: sc.width
            height: 1
            color: Theme.text
            opacity: 0.03
        }
    }
}
