.pragma library

function norm(a) {
    while (a < 0) a += 2 * Math.PI;
    while (a >= 2 * Math.PI) a -= 2 * Math.PI;
    return a;
}
// true when going clockwise (increasing angle) from `from` to `to` passes `m`
function through(from, to, m) { return norm(m - from) <= norm(to - from); }

// Crescent moon outline as a closed path: circle (x, y, r) minus a circle that
// is offset up and to the right. The caller strokes it.
function moon(ctx, x, y, r) {
    var bx = r * 0.55, by = -r * 0.35, rb = r * 0.84;
    var d = Math.sqrt(bx * bx + by * by);
    var a = (r * r - rb * rb + d * d) / (2 * d);
    var h = Math.sqrt(Math.max(0, r * r - a * a));
    var xm = a * bx / d, ym = a * by / d;
    var p1x = xm + h * by / d, p1y = ym - h * bx / d;
    var p2x = xm - h * by / d, p2y = ym + h * bx / d;
    var m = Math.atan2(-by, -bx);
    var a1 = Math.atan2(p1y, p1x), a2 = Math.atan2(p2y, p2x);
    ctx.beginPath();
    ctx.arc(x, y, r, a1, a2, !through(a1, a2, m));
    var b1 = Math.atan2(p2y - by, p2x - bx), b2 = Math.atan2(p1y - by, p1x - bx);
    ctx.arc(x + bx, y + by, rb, b1, b2, !through(b1, b2, m));
    ctx.closePath();
    ctx.stroke();
}
