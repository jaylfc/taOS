// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// PMTiles v3 reader (the single-file tile archive format of Protomaps, BSD-3-Clause spec) and a Mapbox Vector Tile
// decoder. Offline maps are read straight from the file: no server, no network.
#include <QByteArray>
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QPointF>
#include <QPolygonF>
#include <QString>
#include <QVariantMap>
#include <QVector>
#include <memory>

class QFile;

namespace Taos::Survival {

struct PmHeader
{
    quint64 rootOffset = 0, rootLength = 0, metaOffset = 0, metaLength = 0, leafOffset = 0, leafLength = 0;
    quint64 dataOffset = 0, dataLength = 0, addressedTiles = 0, tileEntries = 0, tileContents = 0;
    quint8 internalCompression = 0, tileCompression = 0, tileType = 0, minZoom = 0, maxZoom = 0, centerZoom = 0;
    double minLon = 0, minLat = 0, maxLon = 0, maxLat = 0, centerLon = 0, centerLat = 0;
};

class PmTilesArchive
{
public:
    ~PmTilesArchive();
    static std::unique_ptr<PmTilesArchive> open(const QString &file, QString *error = nullptr);
    const PmHeader &header() const { return m_h; }
    QJsonObject metadata() const { return m_meta; }
    QString name() const;
    QString attribution() const;       // plain text, markup removed
    bool isVector() const { return m_h.tileType == 1; }
    /// The (decompressed) bytes of a tile; empty when the archive has none there.
    QByteArray tile(int z, int x, int y) const;
    static quint64 tileId(int z, int x, int y);

private:
    PmTilesArchive();
    struct Entry { quint64 tileId, offset; quint32 length, runLength; };
    QVector<Entry> readDir(quint64 offset, quint64 length, bool root) const;
    QByteArray readAt(quint64 pos, quint64 len) const;
    QByteArray inflate(const QByteArray &b, quint8 compression) const;
    mutable QMutex m_lock;
    std::unique_ptr<QFile> m_file;
    PmHeader m_h;
    QJsonObject m_meta;
    mutable QHash<quint64, QVector<Entry>> m_dirs;
};

/// gzip or zlib data to bytes; empty on failure or when the result would pass `limit`.
QByteArray gunzip(const QByteArray &in, qsizetype limit = 128 * 1024 * 1024);

struct MvtFeature
{
    int type = 0;                  // 1 point, 2 line, 3 polygon
    QList<QPolygonF> parts;        // tile units (0..extent); polygon rings carry their winding
    QVariantMap props;
    QString str(const char *key) const { return props.value(QLatin1String(key)).toString(); }
};
struct MvtLayer
{
    QString name;
    int extent = 4096;
    QList<MvtFeature> features;
};
struct MvtTile
{
    QList<MvtLayer> layers;
    bool ok = false;
    const MvtLayer *layer(const QString &n) const
    {
        for (const MvtLayer &l : layers) if (l.name == n) return &l;
        return nullptr;
    }
};
MvtTile decodeMvt(const QByteArray &bytes);

/// Web Mercator helpers (the map's own coordinate space is world pixels of a 512 px tile at zoom z).
double lonToWorldX(double lon, double z);
double latToWorldY(double lat, double z);
double worldXToLon(double x, double z);
double worldYToLat(double y, double z);

} // namespace Taos::Survival
