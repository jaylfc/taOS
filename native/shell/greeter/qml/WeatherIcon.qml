import QtQuick
import Taos.Greeter
import "Glyphs.js" as Glyphs

// Outline weather glyphs drawn with the 2D canvas, 1.7 px stroke on a 24 unit
// grid (the same family as the web lock screen).
Canvas {
    id: c
    property string icon: ""
    property color color: Style.text
    width: 32; height: 32
    antialiasing: true
    onIconChanged: requestPaint()
    onColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        var k = width / 24;
        ctx.scale(k, k);
        ctx.strokeStyle = c.color;
        ctx.fillStyle = c.color;
        ctx.lineWidth = 1.7;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        function sun(x, y, r) {
            ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
            for (var i = 0; i < 8; i++) {
                var a = i * Math.PI / 4;
                ctx.beginPath();
                ctx.moveTo(x + Math.cos(a) * (r + 2.2), y + Math.sin(a) * (r + 2.2));
                ctx.lineTo(x + Math.cos(a) * (r + 4), y + Math.sin(a) * (r + 4));
                ctx.stroke();
            }
        }
        function moon(x, y, r) { Glyphs.moon(ctx, x, y, r); }
        function cloud(dx, dy) {
            ctx.beginPath();
            ctx.moveTo(6.5 + dx, 18 + dy);
            ctx.arc(6.5 + dx, 14.5 + dy, 3.5, Math.PI / 2, 3 * Math.PI / 2 - 0.2, false);
            ctx.arc(11.5 + dx, 10 + dy, 4.6, Math.PI * 1.08, Math.PI * 1.9, false);
            ctx.arc(16.8 + dx, 13.2 + dy, 3.4, Math.PI * 1.55, Math.PI * 0.5, false);
            ctx.lineTo(6.5 + dx, 18 + dy);
            ctx.stroke();
        }
        function drops(n, snow) {
            for (var i = 0; i < n; i++) {
                var x = 8 + i * 4;
                ctx.beginPath();
                if (snow) { ctx.arc(x, 21, 0.4, 0, 7); } else { ctx.moveTo(x, 19.5); ctx.lineTo(x - 1.2, 22.5); }
                ctx.stroke();
            }
        }
        switch (icon) {
        case "clear-day": sun(12, 12, 4.4); break;
        case "clear-night": moon(12, 12, 7); break;
        case "partly-cloudy-day": sun(8.2, 8.2, 3); cloud(1.5, 2.5); break;
        case "partly-cloudy-night": moon(8.2, 8.2, 4.4); cloud(1.5, 2.5); break;
        case "cloudy": cloud(0, 0); break;
        case "fog":
            cloud(0, -2.5);
            ctx.beginPath(); ctx.moveTo(5, 18.5); ctx.lineTo(19, 18.5); ctx.moveTo(8, 21.5); ctx.lineTo(16, 21.5); ctx.stroke();
            break;
        case "drizzle": cloud(0, -2); drops(3, false); break;
        case "rain": cloud(0, -2.5); drops(4, false); break;
        case "snow": cloud(0, -2.5); drops(4, true); break;
        case "thunder":
            cloud(0, -2.5);
            ctx.beginPath(); ctx.moveTo(12.5, 15.5); ctx.lineTo(10.2, 19.2); ctx.lineTo(13.2, 19.2); ctx.lineTo(11, 23); ctx.stroke();
            break;
        }
    }
}
