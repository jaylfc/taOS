import QtQuick
import Taos.Greeter
import "Glyphs.js" as Glyphs

// A quiet round glass button (Sleep, Restart, Shut Down). The first activation
// arms it: it widens to "Restart?" for four seconds; the second one runs it.
FocusScope {
    id: pb
    property Item blurred: null
    property string action: ""      // sleep | restart | shutdown
    property string label: ""       // accessible name and tooltip
    property string confirmText: "" // text shown while armed
    property string description: ""
    readonly property bool armed: Power.armed === action
    readonly property real size: 44 * Plan.s
    height: size
    width: armed ? confirmLabel.implicitWidth + 2 * 20 * Plan.s : size
    activeFocusOnTab: true
    Behavior on width { enabled: !Style.reduceMotion; NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

    Accessible.role: Accessible.Button
    Accessible.name: armed ? confirmText : label
    Accessible.description: armed ? qsTr("Activate again to confirm") : description
    Accessible.focusable: true
    Accessible.onPressAction: Power.activate(pb.action)
    Keys.onReturnPressed: Power.activate(pb.action)
    Keys.onEnterPressed: Power.activate(pb.action)
    Keys.onSpacePressed: Power.activate(pb.action)

    GlassPanel {
        anchors.fill: parent
        blurred: pb.blurred
        shadow: false
    }
    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Style.text
        opacity: tap.pressed ? 0.16 : (hover.hovered || pb.armed ? 0.09 : 0)
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 120 } }
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -4 * Plan.s
        radius: height / 2
        color: "transparent"
        border.width: Math.max(2, 2.5 * Plan.s)
        border.color: Style.accent
        visible: pb.activeFocus
    }
    Canvas {
        id: glyph
        anchors.centerIn: parent
        width: 22 * Plan.s; height: width
        visible: !pb.armed
        property color stroke: Style.text
        onStrokeChanged: requestPaint()
        onWidthChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            ctx.scale(width / 24, width / 24);
            ctx.strokeStyle = stroke; ctx.lineWidth = 1.8; ctx.lineCap = "round"; ctx.lineJoin = "round";
            if (pb.action === "sleep") {  // crescent moon
                Glyphs.moon(ctx, 12, 12, 8.2);
                return;
            }
            ctx.beginPath();
            if (pb.action === "restart") {  // circular arrow
                ctx.arc(12, 12, 7.5, -0.35 * Math.PI, 1.35 * Math.PI, false);
                ctx.moveTo(12.2, 1.9); ctx.lineTo(16.2, 4.6); ctx.lineTo(12.4, 7.6);
            } else {  // power symbol
                ctx.arc(12, 12.6, 7.6, -0.32 * Math.PI, 1.32 * Math.PI, false);
                ctx.moveTo(12, 3.5); ctx.lineTo(12, 11.5);
            }
            ctx.stroke();
        }
    }
    Text {
        id: confirmLabel
        anchors.centerIn: parent
        visible: pb.armed
        text: pb.confirmText
        color: Style.text
        font.family: "Inter"; font.weight: Font.DemiBold
        font.pixelSize: 16 * Plan.s
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; onTapped: { pb.forceActiveFocus(); Power.activate(pb.action); } }

    // Tooltip: a small glass chip above the button after a short hover.
    Timer { id: tipDelay; interval: 450; running: hover.hovered && !pb.armed }
    Rectangle {
        id: tip
        visible: opacity > 0.01
        opacity: (tipDelay.running ? 0 : (hover.hovered && !pb.armed || pb.activeFocus && !pb.armed ? 1 : 0))
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 140 } }
        anchors.horizontalCenter: parent.horizontalCenter
                y: -height - 10 * Plan.s
        width: tipText.implicitWidth + 24 * Plan.s
        height: tipText.implicitHeight + 14 * Plan.s
        radius: 10 * Plan.s
        color: Qt.rgba(Style.raised.r, Style.raised.g, Style.raised.b, Style.dark ? 0.90 : 0.95)
        border.width: 1
        border.color: Style.glassStroke
        Text {
            id: tipText
            anchors.centerIn: parent
            text: pb.label
            color: Style.text
            font.family: "Inter"; font.weight: Font.Medium
            font.pixelSize: 14 * Plan.s
        }
    }
}
