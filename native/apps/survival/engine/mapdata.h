// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The map engine: an open PMTiles pack, its tiles turned into drawable shapes (cached), and a place name index built
// from the pack itself. No Qt Quick here, so the CLI and the tests use it too.
#include <QCache>
#include <QMutex>
#include <QPainterPath>
#include <QString>
#include <QVector>
#include <memory>

#include "pmtiles.h"

namespace Taos::Survival {

enum FillClass { FillEarth, FillUrban, FillGreen, FillWater, FillBuilding, FillCount };
enum LineClass { LineWater, LinePath, LineMinor, LineMedium, LineMajor, LineHighway, LineRail, LineBorderCountry, LineBorderRegion, LineCount };
// label classes, most important first
enum LabelClass { LabelCountry, LabelRegion, LabelCity, LabelTown, LabelVillage, LabelSmall, LabelClassCount };

struct MapLabel
{
    QString text;
    QPointF pos;          // tile units
    int cls = LabelSmall;
    double minZoom = 0;   // the zoom from which the label is shown
    double rank = 0;      // lower first
};

struct StyledTile
{
    int extent = 4096;
    bool hasData = false;
    QPainterPath fills[FillCount];
    QPainterPath lines[LineCount];
    QVector<MapLabel> labels;
};

std::shared_ptr<StyledTile> styleTile(const MvtTile &tile);

struct Place
{
    QString name;       // in the person's language when the pack has it
    QString altName;    // the local name when it differs
    QString kind;       // city, town, village, ...
    double lon = 0, lat = 0;
    double rank = 0;
    int zoom = 10;      // a good zoom to look at it
};

class MapData
{
public:
    static std::unique_ptr<MapData> open(const QString &file, QString *error = nullptr);
    const PmTilesArchive &archive() const { return *m_archive; }
    /// The styled tile at z/x/y; null when the pack has no such tile. Cached.
    std::shared_ptr<StyledTile> tile(int z, int x, int y) const;
    /// Builds the place index from the low zoom tiles; `maxZoom` bounds the work (a few seconds).
    void buildPlaceIndex(int maxZoom = 9);
    bool placeIndexReady() const { return m_placesReady; }
    /// Places whose name starts with, or contains, `text`; best first.
    QList<Place> searchPlaces(const QString &text, int limit = 12) const;
    int placeCount() const { return int(m_places.size()); }

private:
    MapData() = default;
    std::unique_ptr<PmTilesArchive> m_archive;
    mutable QMutex m_lock;
    mutable QCache<quint64, std::shared_ptr<StyledTile>> m_cache{600};
    QVector<Place> m_places;
    bool m_placesReady = false;
};

} // namespace Taos::Survival
