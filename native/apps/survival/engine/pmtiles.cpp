// SPDX-License-Identifier: AGPL-3.0-or-later
#include "pmtiles.h"

#include <QFile>
#include <QJsonDocument>
#include <QMutexLocker>
#include <QRegularExpression>
#include <cmath>
#include <cstring>
#include <zlib.h>

namespace Taos::Survival {

namespace {
quint64 rd64(const QByteArray &b, int o) { quint64 v = 0; for (int i = 7; i >= 0; --i) v = (v << 8) | quint8(b[o + i]); return v; }
qint32 rdI32(const QByteArray &b, int o) { return qint32(quint32(quint8(b[o])) | quint32(quint8(b[o + 1])) << 8 | quint32(quint8(b[o + 2])) << 16 | quint32(quint8(b[o + 3])) << 24); }

bool varint(const QByteArray &b, int &i, quint64 &v)
{
    v = 0;
    for (int shift = 0; shift < 64 && i < b.size(); shift += 7) {
        const quint8 c = quint8(b[i++]);
        v |= quint64(c & 0x7f) << shift;
        if (!(c & 0x80))
            return true;
    }
    return false;
}
} // namespace

QByteArray gunzip(const QByteArray &in, qsizetype limit)
{
    if (in.isEmpty())
        return {};
    z_stream s{};
    if (inflateInit2(&s, 15 + 32) != Z_OK)   // gzip or zlib header, detected
        return {};
    QByteArray out;
    s.next_in = reinterpret_cast<Bytef *>(const_cast<char *>(in.constData()));
    s.avail_in = uInt(in.size());
    char buf[32768];
    int rc;
    do {
        s.next_out = reinterpret_cast<Bytef *>(buf);
        s.avail_out = sizeof buf;
        rc = inflate(&s, Z_NO_FLUSH);
        if (rc != Z_OK && rc != Z_STREAM_END) {
            inflateEnd(&s);
            return {};
        }
        out.append(buf, qsizetype(sizeof buf - s.avail_out));
        if (out.size() > limit) {
            inflateEnd(&s);
            return {};
        }
    } while (rc != Z_STREAM_END);
    inflateEnd(&s);
    return out;
}

PmTilesArchive::PmTilesArchive() = default;
PmTilesArchive::~PmTilesArchive() = default;

std::unique_ptr<PmTilesArchive> PmTilesArchive::open(const QString &file, QString *error)
{
    auto fail = [&](const QString &m) -> std::unique_ptr<PmTilesArchive> { if (error) *error = m; return nullptr; };
    std::unique_ptr<PmTilesArchive> a(new PmTilesArchive);
    a->m_file = std::make_unique<QFile>(file);
    if (!a->m_file->open(QIODevice::ReadOnly))
        return fail(QStringLiteral("Cannot open the map file."));
    const QByteArray h = a->m_file->read(127);
    if (h.size() < 127 || !h.startsWith("PMTiles"))
        return fail(QStringLiteral("This is not a PMTiles map file."));
    if (quint8(h[7]) != 3)
        return fail(QStringLiteral("Only PMTiles version 3 maps can be read."));
    PmHeader &p = a->m_h;
    p.rootOffset = rd64(h, 8); p.rootLength = rd64(h, 16); p.metaOffset = rd64(h, 24); p.metaLength = rd64(h, 32);
    p.leafOffset = rd64(h, 40); p.leafLength = rd64(h, 48); p.dataOffset = rd64(h, 56); p.dataLength = rd64(h, 64);
    p.addressedTiles = rd64(h, 72); p.tileEntries = rd64(h, 80); p.tileContents = rd64(h, 88);
    p.internalCompression = quint8(h[97]); p.tileCompression = quint8(h[98]); p.tileType = quint8(h[99]);
    p.minZoom = quint8(h[100]); p.maxZoom = quint8(h[101]);
    p.minLon = rdI32(h, 102) / 1e7; p.minLat = rdI32(h, 106) / 1e7; p.maxLon = rdI32(h, 110) / 1e7; p.maxLat = rdI32(h, 114) / 1e7;
    p.centerZoom = quint8(h[118]); p.centerLon = rdI32(h, 119) / 1e7; p.centerLat = rdI32(h, 123) / 1e7;
    if (p.internalCompression > 2 || (p.tileCompression > 2))
        return fail(QStringLiteral("This map uses a compression (brotli or zstd) that is not supported yet."));
    if (p.rootOffset + p.rootLength > quint64(a->m_file->size()) || p.dataOffset + p.dataLength > quint64(a->m_file->size()))
        return fail(QStringLiteral("This map file is incomplete."));
    const QByteArray meta = a->inflate(a->readAt(p.metaOffset, p.metaLength), p.internalCompression);
    a->m_meta = QJsonDocument::fromJson(meta).object();
    return a;
}

QString PmTilesArchive::name() const { return m_meta.value(QStringLiteral("name")).toString(); }

QString PmTilesArchive::attribution() const
{
    QString a = m_meta.value(QStringLiteral("attribution")).toString();
    a.remove(QRegularExpression(QStringLiteral("<[^>]*>")));
    a.replace(QStringLiteral("&copy;"), QStringLiteral("©")).replace(QStringLiteral("&amp;"), QStringLiteral("&"));
    return a.simplified();
}

QByteArray PmTilesArchive::readAt(quint64 pos, quint64 len) const
{
    QMutexLocker lock(&m_lock);
    if (!m_file->seek(qint64(pos)))
        return {};
    return m_file->read(qint64(len));
}

QByteArray PmTilesArchive::inflate(const QByteArray &b, quint8 compression) const
{
    if (compression == 2)
        return gunzip(b);
    return b;   // 1: none (0 unknown is treated as none)
}

QVector<PmTilesArchive::Entry> PmTilesArchive::readDir(quint64 offset, quint64 length, bool root) const
{
    const quint64 key = root ? 0 : offset + 1;
    {
        QMutexLocker lock(&m_lock);
        auto it = m_dirs.constFind(key);
        if (it != m_dirs.constEnd())
            return *it;
    }
    const QByteArray raw = readAt(root ? m_h.rootOffset : m_h.leafOffset + offset, length);
    const QByteArray b = inflate(raw, m_h.internalCompression);
    QVector<Entry> out;
    int i = 0;
    quint64 n = 0;
    if (!varint(b, i, n) || n > 10'000'000)
        return out;
    out.resize(int(n));
    quint64 last = 0;
    for (int k = 0; k < int(n); ++k) { quint64 d; if (!varint(b, i, d)) return {}; last += d; out[k].tileId = last; }
    for (int k = 0; k < int(n); ++k) { quint64 v; if (!varint(b, i, v)) return {}; out[k].runLength = quint32(v); }
    for (int k = 0; k < int(n); ++k) { quint64 v; if (!varint(b, i, v)) return {}; out[k].length = quint32(v); }
    for (int k = 0; k < int(n); ++k) {
        quint64 v;
        if (!varint(b, i, v)) return {};
        out[k].offset = (v == 0 && k > 0) ? out[k - 1].offset + out[k - 1].length : v - 1;
    }
    QMutexLocker lock(&m_lock);
    m_dirs.insert(key, out);
    return out;
}

quint64 PmTilesArchive::tileId(int z, int x, int y)
{
    quint64 acc = 0;
    for (int t = 0; t < z; ++t)
        acc += quint64(1) << (2 * t);
    const qint64 n = qint64(1) << z;
    qint64 d = 0, tx = x, ty = y;
    for (qint64 s = n / 2; s > 0; s /= 2) {
        const qint64 rx = (tx & s) ? 1 : 0, ry = (ty & s) ? 1 : 0;
        d += s * s * ((3 * rx) ^ ry);
        if (ry == 0) {
            if (rx == 1) { tx = n - 1 - tx; ty = n - 1 - ty; }
            std::swap(tx, ty);
        }
    }
    return acc + quint64(d);
}

QByteArray PmTilesArchive::tile(int z, int x, int y) const
{
    if (z < m_h.minZoom || z > m_h.maxZoom || x < 0 || y < 0 || x >= (1 << z) || y >= (1 << z))
        return {};
    const quint64 id = tileId(z, x, y);
    quint64 offset = m_h.rootOffset, length = m_h.rootLength;
    bool root = true;
    for (int depth = 0; depth < 4; ++depth) {
        const QVector<Entry> dir = readDir(offset, length, root);
        if (dir.isEmpty())
            return {};
        // the last entry whose tileId is <= id
        int lo = 0, hi = dir.size() - 1, found = -1;
        while (lo <= hi) {
            const int mid = (lo + hi) / 2;
            if (dir[mid].tileId <= id) { found = mid; lo = mid + 1; } else hi = mid - 1;
        }
        if (found < 0)
            return {};
        const Entry &e = dir[found];
        if (e.runLength > 0) {
            if (id < e.tileId + e.runLength)
                return inflate(readAt(m_h.dataOffset + e.offset, e.length), m_h.tileCompression);
            return {};
        }
        offset = e.offset;       // a leaf directory
        length = e.length;
        root = false;
    }
    return {};
}

// ---- Mapbox Vector Tile ------------------------------------------------------------------------------------------
namespace {
struct Reader
{
    const QByteArray &b;
    int i = 0;
    int end;
    explicit Reader(const QByteArray &bytes, int from = 0, int to = -1) : b(bytes), i(from), end(to < 0 ? int(bytes.size()) : to) {}
    bool more() const { return i < end; }
    bool vint(quint64 &v)
    {
        v = 0;
        for (int shift = 0; shift < 64 && i < end; shift += 7) {
            const quint8 c = quint8(b[i++]);
            v |= quint64(c & 0x7f) << shift;
            if (!(c & 0x80)) return true;
        }
        return false;
    }
    bool skip(int wire, int *lenStart = nullptr, int *lenEnd = nullptr)
    {
        quint64 v;
        switch (wire) {
        case 0: return vint(v);
        case 1: i += 8; return i <= end;
        case 5: i += 4; return i <= end;
        case 2:
            if (!vint(v) || v > quint64(end - i)) return false;
            if (lenStart) *lenStart = i;
            if (lenEnd) *lenEnd = i + int(v);
            i += int(v);
            return true;
        default: return false;
        }
    }
};
qint32 zigzag(quint32 v) { return qint32((v >> 1) ^ (~(v & 1) + 1)); }

bool decodeGeometry(const QVector<quint32> &g, int type, MvtFeature &f)
{
    QPolygonF cur;
    int x = 0, y = 0;
    size_t i = 0;
    auto flush = [&] { if (!cur.isEmpty()) { f.parts.append(cur); cur.clear(); } };
    while (i < size_t(g.size())) {
        const quint32 ci = g[int(i++)];
        const int cmd = int(ci & 7);
        const quint32 count = ci >> 3;
        if (cmd == 1 || cmd == 2) {
            for (quint32 k = 0; k < count; ++k) {
                if (i + 2 > size_t(g.size())) return false;
                x += zigzag(g[int(i++)]);
                y += zigzag(g[int(i++)]);
                if (cmd == 1 && type != 1) flush();
                cur.append(QPointF(x, y));
                if (type == 1) flush();
            }
        } else if (cmd == 7) {
            if (!cur.isEmpty()) cur.append(cur.first());
            flush();
        } else {
            return false;
        }
    }
    flush();
    return true;
}
} // namespace

MvtTile decodeMvt(const QByteArray &bytes)
{
    MvtTile tile;
    Reader tr(bytes);
    while (tr.more()) {
        quint64 key;
        if (!tr.vint(key)) return tile;
        const int field = int(key >> 3), wire = int(key & 7);
        int ls = 0, le = 0;
        if (field != 3 || wire != 2) { if (!tr.skip(wire)) return tile; continue; }
        if (!tr.skip(2, &ls, &le)) return tile;
        // one layer
        MvtLayer layer;
        QStringList keys;
        QVariantList values;
        QList<QPair<int, int>> featureRanges;
        Reader lr(bytes, ls, le);
        while (lr.more()) {
            quint64 k;
            if (!lr.vint(k)) return tile;
            const int f = int(k >> 3), w = int(k & 7);
            int s = 0, e = 0;
            if (w == 2) {
                if (!lr.skip(2, &s, &e)) return tile;
                if (f == 1) layer.name = QString::fromUtf8(bytes.mid(s, e - s));
                else if (f == 2) featureRanges.append({s, e});
                else if (f == 3) keys.append(QString::fromUtf8(bytes.mid(s, e - s)));
                else if (f == 4) {
                    Reader vr(bytes, s, e);
                    QVariant val;
                    while (vr.more()) {
                        quint64 vk;
                        if (!vr.vint(vk)) break;
                        const int vf = int(vk >> 3), vw = int(vk & 7);
                        int a = 0, c = 0;
                        if (vw == 2) { if (!vr.skip(2, &a, &c)) break; if (vf == 1) val = QString::fromUtf8(bytes.mid(a, c - a)); }
                        else if (vw == 0) { quint64 n; if (!vr.vint(n)) break;
                            if (vf == 4) val = qlonglong(n); else if (vf == 5) val = qulonglong(n);
                            else if (vf == 6) val = qlonglong((n >> 1) ^ (~(n & 1) + 1)); else if (vf == 7) val = n != 0; }
                        else if (vw == 5) { if (vr.i + 4 > vr.end) break; float fl; memcpy(&fl, bytes.constData() + vr.i, 4); vr.i += 4; val = double(fl); }
                        else if (vw == 1) { if (vr.i + 8 > vr.end) break; double d; memcpy(&d, bytes.constData() + vr.i, 8); vr.i += 8; val = d; }
                        else if (!vr.skip(vw)) break;
                    }
                    values.append(val);
                } else {
                    // unknown length-delimited field
                }
            } else if (w == 0) {
                quint64 n;
                if (!lr.vint(n)) return tile;
                if (f == 5) layer.extent = int(n);
            } else if (!lr.skip(w)) {
                return tile;
            }
        }
        for (const auto &r : std::as_const(featureRanges)) {
            MvtFeature feat;
            QVector<quint32> tags, geom;
            Reader fr(bytes, r.first, r.second);
            while (fr.more()) {
                quint64 k;
                if (!fr.vint(k)) break;
                const int f = int(k >> 3), w = int(k & 7);
                if (w == 0) { quint64 n; if (!fr.vint(n)) break; if (f == 3) feat.type = int(n); }
                else if (w == 2) {
                    int s = 0, e = 0;
                    if (!fr.skip(2, &s, &e)) break;
                    if (f == 2 || f == 4) {
                        Reader pr(bytes, s, e);
                        quint64 n;
                        while (pr.more() && pr.vint(n))
                            (f == 2 ? tags : geom).append(quint32(n));
                    }
                } else if (!fr.skip(w)) break;
            }
            for (int t = 0; t + 1 < tags.size(); t += 2)
                if (int(tags[t]) < keys.size() && int(tags[t + 1]) < values.size())
                    feat.props.insert(keys[int(tags[t])], values[int(tags[t + 1])]);
            if (decodeGeometry(geom, feat.type, feat))
                layer.features.append(feat);
        }
        tile.layers.append(layer);
    }
    tile.ok = true;
    return tile;
}

double lonToWorldX(double lon, double z) { return (lon + 180.0) / 360.0 * 512.0 * std::pow(2.0, z); }
double latToWorldY(double lat, double z)
{
    const double s = std::sin(std::clamp(lat, -85.0511, 85.0511) * M_PI / 180.0);
    return (0.5 - std::log((1 + s) / (1 - s)) / (4 * M_PI)) * 512.0 * std::pow(2.0, z);
}
double worldXToLon(double x, double z) { return x / (512.0 * std::pow(2.0, z)) * 360.0 - 180.0; }
double worldYToLat(double y, double z)
{
    const double n = M_PI - 2.0 * M_PI * y / (512.0 * std::pow(2.0, z));
    return 180.0 / M_PI * std::atan(0.5 * (std::exp(n) - std::exp(-n)));
}

} // namespace Taos::Survival
