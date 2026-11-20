import QtQuick
import Taos.Style 1.0

// A live line graph of the last samples (two minutes at the 2 s cadence), with
// an area fill and a smooth slide when a sample arrives (no slide with reduced
// motion). It draws what the engine hands it; it keeps no history itself.
// `title` and `format` give the text equivalent read by assistive technology.
Item {
    id: g
    property var values: []
    property var values2: []
    property string title: ""
    property string title2: ""
    property color color: Pal.accent
    property color color2: Pal.info
    property real maxValue: 100          // 0 = scale to the data
    property int samples: 60
    property var format: function (v) { return Math.round(v) + "%" }
    property real t: 1
    implicitHeight: 84
    implicitWidth: 240

    readonly property string summary: {
        function part(vals, name) {
            if (!vals || vals.length === 0)
                return name + ": no samples yet"
            var sum = 0, peak = 0
            for (var i = 0; i < vals.length; ++i) { sum += vals[i]; peak = Math.max(peak, vals[i]) }
            return name + ": now " + g.format(vals[vals.length - 1]) + ", average " + g.format(sum / vals.length)
                 + ", peak " + g.format(peak)
        }
        var s = part(g.values, g.title)
        if (g.values2 && g.title2.length > 0)
            s += ". " + part(g.values2, g.title2)
        return s + ", over the last " + Math.round((g.values ? g.values.length : 0) * 2) + " seconds"
    }
    Accessible.role: Accessible.Graphic
    Accessible.name: title.length > 0 ? title + " graph" : "Graph"
    Accessible.description: summary

    function autoMax() {
        var m = 0
        var all = [values, values2]
        for (var a = 0; a < all.length; ++a)
            for (var i = 0; all[a] && i < all[a].length; ++i) m = Math.max(m, all[a][i])
        return m <= 0 ? 1 : m * 1.2
    }

    NumberAnimation on t { id: slide; running: false; from: 0; to: 1; duration: Pal.dur(520); easing.type: Easing.OutCubic }
    onValuesChanged: { if (Pal.dur(1) === 0) { t = 1 } else { slide.restart() } cv.requestPaint() }
    onValues2Changed: cv.requestPaint()
    onTChanged: cv.requestPaint()
    onColorChanged: cv.requestPaint()

    Canvas {
        id: cv
        anchors.fill: parent
        renderStrategy: Canvas.Cooperative
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var w = width, h = height
            if (w < 4 || h < 4) return
            ctx.lineWidth = 1
            ctx.strokeStyle = Pal.hairline
            for (var i = 1; i < 4; ++i) {
                var gy = Math.round(h * i / 4) + 0.5
                ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke()
            }
            var mx = g.maxValue > 0 ? g.maxValue : g.autoMax()
            var step = w / Math.max(1, g.samples - 1)
            function draw(vals, col, fill) {
                if (!vals || vals.length < 2) return
                var n = vals.length, px = [], py = []
                for (var i = 0; i < n; ++i) {
                    var k = n - 1 - i
                    px.push(w - (k - (1 - g.t)) * step)
                    py.push(h - 3 - Math.min(Math.max(vals[i], 0), mx) / mx * (h - 8))
                }
                ctx.beginPath()
                ctx.moveTo(px[0], py[0])
                for (var j = 1; j < n; ++j) {
                    var mxp = (px[j - 1] + px[j]) / 2, myp = (py[j - 1] + py[j]) / 2
                    ctx.quadraticCurveTo(px[j - 1], py[j - 1], mxp, myp)
                }
                ctx.lineTo(px[n - 1], py[n - 1])
                ctx.lineJoin = "round"
                ctx.lineWidth = 2
                ctx.strokeStyle = col
                ctx.stroke()
                if (fill) {
                    ctx.lineTo(px[n - 1], h); ctx.lineTo(px[0], h); ctx.closePath()
                    var gr = ctx.createLinearGradient(0, 0, 0, h)
                    gr.addColorStop(0, Pal.alpha(col, 0.30))
                    gr.addColorStop(1, Pal.alpha(col, 0.0))
                    ctx.fillStyle = gr
                    ctx.fill()
                }
            }
            draw(g.values, g.color, true)
            draw(g.values2, g.color2, true)
        }
    }
}
