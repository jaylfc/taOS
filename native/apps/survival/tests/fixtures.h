// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Test fixtures built inside the tests: a small ZIM (with libzim's own writer), a small PMTiles map and the vector
// tile in it. No binary is committed and nothing is downloaded.
// gnu++17 defines `unix` and `linux` as macros; libzim has a namespace called unix
#undef unix
#undef linux
#include <zim/writer/contentProvider.h>
#include <zim/writer/creator.h>
#include <zim/writer/item.h>
#include <zlib.h>

#include <QByteArray>
#include <QDir>
#include <QFile>
#include <QString>
#include <QtGlobal>
#include <memory>

namespace Fixture {

class ZimItem : public zim::writer::Item
{
public:
    ZimItem(std::string path, std::string title, std::string mime, std::string data, bool front = true)
        : m_path(std::move(path)), m_title(std::move(title)), m_mime(std::move(mime)), m_data(std::move(data)), m_front(front) {}
    std::string getPath() const override { return m_path; }
    std::string getTitle() const override { return m_title; }
    std::string getMimeType() const override { return m_mime; }
    std::unique_ptr<zim::writer::ContentProvider> getContentProvider() const override
    {
        return std::make_unique<zim::writer::StringProvider>(m_data);
    }
    zim::writer::Hints getHints() const override { return {{zim::writer::FRONT_ARTICLE, m_front ? 1u : 0u}}; }

private:
    std::string m_path, m_title, m_mime, m_data;
    bool m_front;
};

// A 1x1 PNG
inline QByteArray tinyPng()
{
    return QByteArray::fromBase64("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==");
}

/// Writes a ZIM with a main page, an article about water, one about fire, a picture and the usual metadata.
inline bool writeZim(const QString &file)
{
    try {
        zim::writer::Creator c;
        c.configIndexing(true, "en");
        c.startZimCreation(file.toStdString());
        c.addItem(std::make_shared<ZimItem>("index", "Start", "text/html",
            "<html><head><title>Start</title></head><body><h1>Start</h1><p>Read about <a href=\"water\">water</a> and <a href=\"fire\">fire</a>.</p></body></html>"));
        c.addItem(std::make_shared<ZimItem>("water", "Water purification", "text/html",
            "<html><head><title>Water purification</title><script>alert(1)</script></head><body><h1>Water purification</h1>"
            "<p>Boil the water for one minute. Boiling kills bacteria and viruses.</p>"
            "<img src=\"../I/drop.png\" width=\"1\" height=\"1\"><img src=\"http://evil.example/x.png\"></body></html>"));
        c.addItem(std::make_shared<ZimItem>("fire", "Fire lighting", "text/html",
            "<html><body><h1>Fire lighting</h1><p>A bow drill needs dry wood and patience.</p></body></html>"));
        c.addItem(std::make_shared<ZimItem>("I/drop.png", "drop", "image/png", std::string(tinyPng().constData(), size_t(tinyPng().size())), false));
        c.addMetadata("Title", "Test library");
        c.addMetadata("Description", "A pack for the tests");
        c.addMetadata("Language", "eng");
        c.addMetadata("Creator", "Tests");
        c.addMetadata("Publisher", "Tests");
        c.addMetadata("Date", "2026-11-25");
        c.setMainPath("index");
        c.finishZimCreation();
        return QFile::exists(file);
    } catch (const std::exception &) {
        return false;
    }
}

// ---- vector tile and PMTiles ------------------------------------------------------------------------------------
inline void putVarint(QByteArray &b, quint64 v)
{
    do { quint8 c = v & 0x7f; v >>= 7; if (v) c |= 0x80; b.append(char(c)); } while (v);
}
inline void putKey(QByteArray &b, int field, int wire) { putVarint(b, quint64(field << 3 | wire)); }
inline void putBytes(QByteArray &b, int field, const QByteArray &d) { putKey(b, field, 2); putVarint(b, quint64(d.size())); b.append(d); }
inline quint32 zz(int v) { return quint32((v << 1) ^ (v >> 31)); }

struct FeatureSpec
{
    int type;                  // 1 point, 2 line, 3 polygon
    QList<QPair<int, int>> pts;
    QList<QPair<QString, QString>> props;
};

inline QByteArray mvtLayer(const QString &name, const QList<FeatureSpec> &features)
{
    QByteArray layer;
    putKey(layer, 15, 0); putVarint(layer, 2);
    putBytes(layer, 1, name.toUtf8());
    QStringList keys;
    QStringList values;
    QByteArray feats;
    for (const FeatureSpec &f : features) {
        QByteArray fb, tags, geom;
        for (const auto &p : f.props) {
            if (!keys.contains(p.first)) keys << p.first;
            if (!values.contains(p.second)) values << p.second;
            putVarint(tags, quint64(keys.indexOf(p.first)));
            putVarint(tags, quint64(values.indexOf(p.second)));
        }
        if (!tags.isEmpty()) putBytes(fb, 2, tags);
        putKey(fb, 3, 0); putVarint(fb, quint64(f.type));
        int cx = 0, cy = 0;
        if (f.type == 1) {
            putVarint(geom, 1 | (1 << 3));
            putVarint(geom, zz(f.pts[0].first)); putVarint(geom, zz(f.pts[0].second));
        } else {
            putVarint(geom, 1 | (1 << 3));
            putVarint(geom, zz(f.pts[0].first)); putVarint(geom, zz(f.pts[0].second));
            cx = f.pts[0].first; cy = f.pts[0].second;
            putVarint(geom, 2 | (quint32(f.pts.size() - 1) << 3));
            for (int i = 1; i < f.pts.size(); ++i) {
                putVarint(geom, zz(f.pts[i].first - cx)); putVarint(geom, zz(f.pts[i].second - cy));
                cx = f.pts[i].first; cy = f.pts[i].second;
            }
            if (f.type == 3) putVarint(geom, 7 | (1 << 3));
        }
        putBytes(fb, 4, geom);
        putBytes(feats, 2, fb);
    }
    layer.append(feats);
    for (const QString &k : keys) putBytes(layer, 3, k.toUtf8());
    for (const QString &v : values) { QByteArray vb; putBytes(vb, 1, v.toUtf8()); putBytes(layer, 4, vb); }
    putKey(layer, 5, 0); putVarint(layer, 4096);
    return layer;
}

/// One tile: land, a lake, a road and a city called Testville (local name Teststadt).
inline QByteArray sampleMvt()
{
    QByteArray tile;
    putBytes(tile, 3, mvtLayer(QStringLiteral("earth"), {{3, {{0, 0}, {4096, 0}, {4096, 4096}, {0, 4096}}, {{QStringLiteral("kind"), QStringLiteral("earth")}}}}));
    putBytes(tile, 3, mvtLayer(QStringLiteral("water"), {{3, {{1000, 1000}, {2000, 1000}, {2000, 2000}, {1000, 2000}}, {{QStringLiteral("kind"), QStringLiteral("lake")}}}}));
    putBytes(tile, 3, mvtLayer(QStringLiteral("roads"), {{2, {{0, 3000}, {4096, 3000}}, {{QStringLiteral("kind"), QStringLiteral("major_road")}}}}));
    putBytes(tile, 3, mvtLayer(QStringLiteral("places"), {{1, {{2048, 2048}}, {{QStringLiteral("kind"), QStringLiteral("locality")}, {QStringLiteral("kind_detail"), QStringLiteral("city")},
                                                                               {QStringLiteral("name"), QStringLiteral("Teststadt")}, {QStringLiteral("name:en"), QStringLiteral("Testville")}}}}));
    return tile;
}

inline QByteArray gzip(const QByteArray &in)
{
    z_stream s{};
    deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED, 15 + 16, 8, Z_DEFAULT_STRATEGY);
    QByteArray out(int(deflateBound(&s, uLong(in.size()))) + 32, 0);
    s.next_in = reinterpret_cast<Bytef *>(const_cast<char *>(in.constData()));
    s.avail_in = uInt(in.size());
    s.next_out = reinterpret_cast<Bytef *>(out.data());
    s.avail_out = uInt(out.size());
    deflate(&s, Z_FINISH);
    out.resize(int(s.total_out));
    deflateEnd(&s);
    return out;
}

inline void le64(QByteArray &b, int off, quint64 v) { for (int i = 0; i < 8; ++i) b[off + i] = char((v >> (8 * i)) & 0xff); }
inline void le32(QByteArray &b, int off, qint32 v) { for (int i = 0; i < 4; ++i) b[off + i] = char((quint32(v) >> (8 * i)) & 0xff); }

/// A PMTiles v3 file with the one tile z0/0/0 (gzip tile compression, uncompressed directory).
inline bool writePmtiles(const QString &file, bool gz = true)
{
    const QByteArray tile = gz ? gzip(sampleMvt()) : sampleMvt();
    QByteArray dir;
    putVarint(dir, 1);                       // one entry
    putVarint(dir, 0);                       // tile id 0 (delta)
    putVarint(dir, 1);                       // run length
    putVarint(dir, quint64(tile.size()));    // length
    putVarint(dir, 1);                       // offset + 1
    const QByteArray meta = "{\"name\":\"Test map\",\"attribution\":\"<a href=\\\"https://example.org\\\">&copy; Test contributors</a>\"}";
    QByteArray h(127, 0);
    h.replace(0, 7, "PMTiles");
    h[7] = 3;
    le64(h, 8, 127); le64(h, 16, quint64(dir.size()));
    le64(h, 24, 127 + quint64(dir.size())); le64(h, 32, quint64(meta.size()));
    le64(h, 40, 0); le64(h, 48, 0);
    le64(h, 56, 127 + quint64(dir.size()) + quint64(meta.size())); le64(h, 64, quint64(tile.size()));
    le64(h, 72, 1); le64(h, 80, 1); le64(h, 88, 1);
    h[96] = 1; h[97] = 1; h[98] = char(gz ? 2 : 1); h[99] = 1;   // clustered, no internal compression, tile compression, MVT
    h[100] = 0; h[101] = 0;
    le32(h, 102, -1800000000); le32(h, 106, -850000000); le32(h, 110, 1800000000); le32(h, 114, 850000000);
    h[118] = 0; le32(h, 119, 0); le32(h, 123, 0);
    QFile f(file);
    if (!f.open(QIODevice::WriteOnly))
        return false;
    f.write(h); f.write(dir); f.write(meta); f.write(tile);
    return true;
}

} // namespace Fixture
