// SPDX-License-Identifier: AGPL-3.0-or-later
#include "qrcode.h"

#include <QObject>
#include <vector>

namespace Taos::Survival {

namespace {

const int kEccPerBlock[2][41] = {
    {-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
    {-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28}};
const int kBlocks[2][41] = {
    {-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25},
    {-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49}};

int rawModules(int ver)
{
    int result = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
        const int na = ver / 7 + 2;
        result -= (25 * na - 10) * na - 55;
        if (ver >= 7)
            result -= 36;
    }
    return result;
}
int dataCodewords(int ver, int ecc) { return rawModules(ver) / 8 - kEccPerBlock[ecc][ver] * kBlocks[ecc][ver]; }

// ---- Reed-Solomon over GF(256), polynomial 0x11D
int gfMul(int x, int y)
{
    int z = 0;
    for (int i = 7; i >= 0; --i) {
        z = (z << 1) ^ ((z >> 7) * 0x11D);
        z ^= ((y >> i) & 1) * x;
    }
    return z;
}
std::vector<quint8> rsDivisor(int degree)
{
    std::vector<quint8> result(size_t(degree), 0);
    result[size_t(degree) - 1] = 1;
    int root = 1;
    for (int i = 0; i < degree; ++i) {
        for (size_t j = 0; j < result.size(); ++j) {
            result[j] = quint8(gfMul(result[j], root));
            if (j + 1 < result.size())
                result[j] ^= result[j + 1];
        }
        root = gfMul(root, 2);
    }
    return result;
}
std::vector<quint8> rsRemainder(const std::vector<quint8> &data, const std::vector<quint8> &div)
{
    std::vector<quint8> result(div.size(), 0);
    for (quint8 b : data) {
        const int factor = b ^ result.front();
        result.erase(result.begin());
        result.push_back(0);
        for (size_t i = 0; i < result.size(); ++i)
            result[i] ^= quint8(gfMul(div[i], factor));
    }
    return result;
}

struct Grid
{
    int size;
    std::vector<std::vector<bool>> m, fn;   // modules, function-pattern flags
    explicit Grid(int s) : size(s), m(size_t(s), std::vector<bool>(size_t(s), false)), fn(size_t(s), std::vector<bool>(size_t(s), false)) {}
    void set(int x, int y, bool dark) { m[size_t(y)][size_t(x)] = dark; fn[size_t(y)][size_t(x)] = true; }
    bool get(int x, int y) const { return m[size_t(y)][size_t(x)]; }
    bool isFn(int x, int y) const { return fn[size_t(y)][size_t(x)]; }
};

void drawFinder(Grid &g, int cx, int cy)
{
    for (int dy = -4; dy <= 4; ++dy)
        for (int dx = -4; dx <= 4; ++dx) {
            const int dist = qMax(qAbs(dx), qAbs(dy));
            const int x = cx + dx, y = cy + dy;
            if (x >= 0 && x < g.size && y >= 0 && y < g.size)
                g.set(x, y, dist != 2 && dist != 4);
        }
}
void drawAlign(Grid &g, int cx, int cy)
{
    for (int dy = -2; dy <= 2; ++dy)
        for (int dx = -2; dx <= 2; ++dx)
            g.set(cx + dx, cy + dy, qMax(qAbs(dx), qAbs(dy)) != 1);
}
std::vector<int> alignPositions(int ver)
{
    if (ver == 1)
        return {};
    const int n = ver / 7 + 2;
    const int step = ver == 32 ? 26 : (ver * 4 + n * 2 + 1) / (n * 2 - 2) * 2;
    std::vector<int> r(static_cast<size_t>(n), 0);
    r[0] = 6;
    for (int i = n - 1, pos = ver * 4 + 10; i >= 1; --i, pos -= step)
        r[size_t(i)] = pos;
    return r;
}
void drawFormat(Grid &g, int ecc, int mask)
{
    const int eccBits = ecc == 0 ? 1 : 0;   // L = 01, M = 00
    const int data = eccBits << 3 | mask;
    int rem = data;
    for (int i = 0; i < 10; ++i)
        rem = (rem << 1) ^ ((rem >> 9) * 0x537);
    const int bits = (data << 10 | rem) ^ 0x5412;
    for (int i = 0; i <= 5; ++i) g.set(8, i, (bits >> i) & 1);
    g.set(8, 7, (bits >> 6) & 1);
    g.set(8, 8, (bits >> 7) & 1);
    g.set(7, 8, (bits >> 8) & 1);
    for (int i = 9; i < 15; ++i) g.set(14 - i, 8, (bits >> i) & 1);
    for (int i = 0; i < 8; ++i) g.set(g.size - 1 - i, 8, (bits >> i) & 1);
    for (int i = 8; i < 15; ++i) g.set(8, g.size - 15 + i, (bits >> i) & 1);
    g.set(8, g.size - 8, true);
}
void drawVersion(Grid &g, int ver)
{
    if (ver < 7)
        return;
    int rem = ver;
    for (int i = 0; i < 12; ++i)
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25);
    const long bits = long(ver) << 12 | rem;
    for (int i = 0; i < 18; ++i) {
        const bool b = (bits >> i) & 1;
        const int a = g.size - 11 + i % 3, c = i / 3;
        g.set(a, c, b);
        g.set(c, a, b);
    }
}
void drawFunctionPatterns(Grid &g, int ver)
{
    for (int i = 0; i < g.size; ++i) {
        g.set(6, i, i % 2 == 0);
        g.set(i, 6, i % 2 == 0);
    }
    drawFinder(g, 3, 3);
    drawFinder(g, g.size - 4, 3);
    drawFinder(g, 3, g.size - 4);
    const auto ap = alignPositions(ver);
    const int n = int(ap.size());
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < n; ++j)
            if (!((i == 0 && j == 0) || (i == 0 && j == n - 1) || (i == n - 1 && j == 0)))
                drawAlign(g, ap[size_t(i)], ap[size_t(j)]);
    drawFormat(g, 0, 0);   // placeholder to reserve the area
    drawVersion(g, ver);
}
void drawCodewords(Grid &g, const std::vector<quint8> &data)
{
    size_t i = 0;
    for (int right = g.size - 1; right >= 1; right -= 2) {
        if (right == 6)
            right = 5;
        for (int vert = 0; vert < g.size; ++vert)
            for (int j = 0; j < 2; ++j) {
                const int x = right - j;
                const bool up = ((right + 1) & 2) == 0;
                const int y = up ? g.size - 1 - vert : vert;
                if (!g.isFn(x, y) && i < data.size() * 8) {
                    g.m[size_t(y)][size_t(x)] = ((data[i >> 3] >> (7 - (i & 7))) & 1) != 0;
                    ++i;
                }
            }
    }
}
bool maskBit(int mask, int x, int y)
{
    switch (mask) {
    case 0: return (x + y) % 2 == 0;
    case 1: return y % 2 == 0;
    case 2: return x % 3 == 0;
    case 3: return (x + y) % 3 == 0;
    case 4: return (x / 3 + y / 2) % 2 == 0;
    case 5: return x * y % 2 + x * y % 3 == 0;
    case 6: return (x * y % 2 + x * y % 3) % 2 == 0;
    default: return ((x + y) % 2 + x * y % 3) % 2 == 0;
    }
}
void applyMask(Grid &g, int mask)
{
    for (int y = 0; y < g.size; ++y)
        for (int x = 0; x < g.size; ++x)
            if (!g.isFn(x, y) && maskBit(mask, x, y))
                g.m[size_t(y)][size_t(x)] = !g.m[size_t(y)][size_t(x)];
}
struct FinderPenalty
{
    int size;
    int h[7] = {0, 0, 0, 0, 0, 0, 0};
    explicit FinderPenalty(int s) : size(s) {}
    void add(int run)
    {
        if (h[0] == 0)
            run += size;
        for (int i = 6; i >= 1; --i)
            h[i] = h[i - 1];
        h[0] = run;
    }
    int count() const
    {
        const int n = h[1];
        const bool core = n > 0 && h[2] == n && h[3] == n * 3 && h[4] == n && h[5] == n;
        return (core && h[0] >= n * 4 && h[6] >= n ? 1 : 0) + (core && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
    }
    int end(bool color, int run)
    {
        if (color) {
            add(run);
            run = 0;
        }
        run += size;
        add(run);
        return count();
    }
};

int penalty(const Grid &g)
{
    const int n = g.size;
    int result = 0;
    for (int pass = 0; pass < 2; ++pass)
        for (int a = 0; a < n; ++a) {
            bool color = false;
            int run = 0;
            FinderPenalty fp(n);
            for (int b = 0; b < n; ++b) {
                const bool v = pass == 0 ? g.get(b, a) : g.get(a, b);
                if (v == color) {
                    ++run;
                    if (run == 5) result += 3;
                    else if (run > 5) result += 1;
                } else {
                    fp.add(run);
                    if (!color)
                        result += fp.count() * 40;
                    color = v;
                    run = 1;
                }
            }
            result += fp.end(color, run) * 40;
        }
    // rule 2: 2x2 blocks
    for (int y = 0; y < n - 1; ++y)
        for (int x = 0; x < n - 1; ++x) {
            const bool c = g.get(x, y);
            if (c == g.get(x + 1, y) && c == g.get(x, y + 1) && c == g.get(x + 1, y + 1))
                result += 3;
        }
    // rule 4: balance
    int dark = 0;
    for (int y = 0; y < n; ++y)
        for (int x = 0; x < n; ++x)
            dark += g.get(x, y) ? 1 : 0;
    const int total = n * n;
    const int k = (qAbs(dark * 20 - total * 10) + total - 1) / total - 1;
    result += k * 10;
    return result;
}

} // namespace

QrResult qrEncode(const QByteArray &data, bool eccHigh)
{
    QrResult out;
    const int ecc = eccHigh ? 1 : 0;
    int ver = 1;
    int usedBits = 0;
    for (;; ++ver) {
        if (ver > 40) {
            out.error = QObject::tr("This is too long for a QR code.");
            return out;
        }
        const int ccBits = ver <= 9 ? 8 : 16;
        usedBits = 4 + ccBits + int(data.size()) * 8;
        if (ccBits == 8 && data.size() > 255)
            continue;
        if (usedBits <= dataCodewords(ver, ecc) * 8)
            break;
    }
    std::vector<bool> bits;
    auto put = [&](quint32 val, int len) { for (int i = len - 1; i >= 0; --i) bits.push_back(((val >> i) & 1) != 0); };
    put(0x4, 4);
    put(quint32(data.size()), ver <= 9 ? 8 : 16);
    for (char c : data)
        put(quint8(c), 8);
    const size_t cap = size_t(dataCodewords(ver, ecc)) * 8;
    for (int i = 0; i < 4 && bits.size() < cap; ++i)
        bits.push_back(false);
    while (bits.size() % 8)
        bits.push_back(false);
    for (quint8 pad = 0xEC; bits.size() < cap; pad ^= 0xEC ^ 0x11)
        put(pad, 8);
    std::vector<quint8> cw(bits.size() / 8, 0);
    for (size_t i = 0; i < bits.size(); ++i)
        cw[i >> 3] |= quint8(bits[i] ? 1 << (7 - (i & 7)) : 0);
    // blocks, error correction, interleave
    const int nb = kBlocks[ecc][ver], bl = kEccPerBlock[ecc][ver], raw = rawModules(ver) / 8;
    const int numShort = nb - raw % nb, shortLen = raw / nb;
    std::vector<std::vector<quint8>> blocks;
    const auto div = rsDivisor(bl);
    size_t k = 0;
    for (int i = 0; i < nb; ++i) {
        const int len = shortLen - bl + (i < numShort ? 0 : 1);
        std::vector<quint8> d(cw.begin() + long(k), cw.begin() + long(k) + len);
        k += size_t(len);
        const auto e = rsRemainder(d, div);
        if (i < numShort)
            d.push_back(0);
        d.insert(d.end(), e.begin(), e.end());
        blocks.push_back(d);
    }
    std::vector<quint8> all;
    for (size_t i = 0; i < blocks[0].size(); ++i)
        for (size_t j = 0; j < blocks.size(); ++j)
            if (i != size_t(shortLen - bl) || int(j) >= numShort)
                all.push_back(blocks[j][i]);
    Grid g(ver * 4 + 17);
    drawFunctionPatterns(g, ver);
    drawCodewords(g, all);
    int best = 0, bestPen = 1 << 30;
    for (int m = 0; m < 8; ++m) {
        applyMask(g, m);
        drawFormat(g, ecc, m);
        const int p = penalty(g);
        if (p < bestPen) { bestPen = p; best = m; }
        applyMask(g, m);
    }
    applyMask(g, best);
    drawFormat(g, ecc, best);
    out.ok = true;
    out.size = g.size;
    out.version = ver;
    for (int y = 0; y < g.size; ++y) {
        QList<bool> row;
        for (int x = 0; x < g.size; ++x)
            row.append(g.get(x, y));
        out.modules.append(row);
    }
    return out;
}

QStringList qrRows(const QrResult &r)
{
    QStringList rows;
    for (const auto &row : r.modules) {
        QString s;
        for (bool b : row)
            s += b ? QLatin1Char('1') : QLatin1Char('0');
        rows << s;
    }
    return rows;
}

} // namespace Taos::Survival
