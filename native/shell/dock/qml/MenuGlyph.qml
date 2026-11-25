// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock

// The two small marks of the dock menu, drawn from rectangles in the theme's text colour: a chevron (this row opens a
// submenu) and a tick (this is where the window is now). Each arm is a bar turned about its own centre.
Item {
    id: g
    property string kind: "chevron"   // chevron | check
    property color color: Theme.text
    width: 14
    height: 14
    // chevron: tip at (9.8, 7), arms 6.4 long
    Rectangle { visible: g.kind === "chevron"; x: 4.34; y: 3.89; width: 6.4; height: 1.7; radius: 0.85; rotation: 45; color: g.color }
    Rectangle { visible: g.kind === "chevron"; x: 4.34; y: 8.41; width: 6.4; height: 1.7; radius: 0.85; rotation: -45; color: g.color }
    // check: joint at (5.4, 10), a short arm up and left, a long arm up and right
    Rectangle { visible: g.kind === "check"; x: 2.33; y: 7.83; width: 3.6; height: 1.8; radius: 0.9; rotation: 45; color: g.color }
    Rectangle { visible: g.kind === "check"; x: 3.97; y: 6.04; width: 8; height: 1.8; radius: 0.9; rotation: -50; color: g.color }
}
