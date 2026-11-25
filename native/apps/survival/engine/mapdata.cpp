// SPDX-License-Identifier: AGPL-3.0-or-later
#include "mapdata.h"

#include <QHash>
#include <QLocale>
#include <QMutexLocker>
#include <QSet>
#include <algorithm>
#include <cmath>

namespace Taos::Survival {

namespace {
void addPolygons(QPainterPath &path, const MvtFeature &f)
{
    for (const QPolygonF &ring : f.parts) {
        if (ring.size() < 3)
            continue;
        path.addPolygon(ring);
        path.closeSubpath();
    }
}
void addLines(QPainterPath &path, const MvtFeature &f)
{
    for (const QPolygonF &line : f.parts) {
        if (line.size() < 2)
            continue;
        path.moveTo(line.first());
        for (int i = 1; i < line.size(); ++i)
            path.lineTo(line[i]);
    }
}
bool isGreen(const QString &k)
{
    static const QSet<QString> s{QStringLiteral("park"), QStringLiteral("forest"), QStringLiteral("wood"), QStringLiteral("nature_reserve"),
        QStringLiteral("garden"), QStringLiteral("grass"), QStringLiteral("meadow"), QStringLiteral("golf_course"), QStringLiteral("cemetery"),
        QStringLiteral("recreation_ground"), QStringLiteral("pitch"), QStringLiteral("village_green"), QStringLiteral("grassland"),
        QStringLiteral("scrub"), QStringLiteral("heath"), QStringLiteral("wetland"), QStringLiteral("national_park"), QStringLiteral("playground"),
        QStringLiteral("zoo"), QStringLiteral("protected_area"), QStringLiteral("allotments")};
    return s.contains(k);
}
bool isUrban(const QString &k)
{
    static const QSet<QString> s{QStringLiteral("residential"), QStringLiteral("industrial"), QStringLiteral("commercial"), QStringLiteral("retail"),
        QStringLiteral("hospital"), QStringLiteral("school"), QStringLiteral("university"), QStringLiteral("college"), QStringLiteral("urban_area"),
        QStringLiteral("military"), QStringLiteral("railway"), QStringLiteral("aerodrome"), QStringLiteral("parking"), QStringLiteral("construction")};
    return s.contains(k);
}
// The name in the person's language when the pack has it (name:en, name:fr, ...), the local name otherwise.
QString localName(const QVariantMap &props)
{
    static const QString lang = QLocale::system().bcp47Name().section(u'-', 0, 0);
    const QString pref = props.value(QStringLiteral("name:") + lang).toString();
    return pref.isEmpty() ? props.value(QStringLiteral("name")).toString() : pref;
}
double num(const QVariant &v, double d) { bool ok = false; const double x = v.toDouble(&ok); return ok ? x : d; }
} // namespace

std::shared_ptr<StyledTile> styleTile(const MvtTile &t)
{
    auto st = std::make_shared<StyledTile>();
    for (int i = 0; i < FillCount; ++i) st->fills[i].setFillRule(Qt::WindingFill);
    for (const MvtLayer &layer : t.layers) {
        st->extent = layer.extent;
        const QString &n = layer.name;
        for (const MvtFeature &f : layer.features) {
            const QString kind = f.str("kind"), detail = f.str("kind_detail");
            st->hasData = true;
            if (n == QLatin1String("earth")) {
                if (f.type == 3) addPolygons(st->fills[FillEarth], f);
            } else if (n == QLatin1String("water")) {
                if (f.type == 3) addPolygons(st->fills[FillWater], f);
                else if (f.type == 2) addLines(st->lines[LineWater], f);
            } else if (n == QLatin1String("landuse") || n == QLatin1String("natural") || n == QLatin1String("landcover")) {
                if (f.type != 3) continue;
                if (kind == QLatin1String("water") || kind == QLatin1String("bay")) addPolygons(st->fills[FillWater], f);
                else if (isGreen(kind)) addPolygons(st->fills[FillGreen], f);
                else if (isUrban(kind)) addPolygons(st->fills[FillUrban], f);
            } else if (n == QLatin1String("buildings")) {
                if (f.type == 3) addPolygons(st->fills[FillBuilding], f);
            } else if (n == QLatin1String("roads")) {
                if (f.type != 2) continue;
                if (kind == QLatin1String("highway")) addLines(st->lines[detail == QLatin1String("motorway") || detail == QLatin1String("trunk") ? LineHighway : LineMajor], f);
                else if (kind == QLatin1String("major_road")) addLines(st->lines[LineMajor], f);
                else if (kind == QLatin1String("medium_road")) addLines(st->lines[LineMedium], f);
                else if (kind == QLatin1String("minor_road")) addLines(st->lines[LineMinor], f);
                else if (kind == QLatin1String("path")) addLines(st->lines[LinePath], f);
                else if (kind == QLatin1String("rail")) addLines(st->lines[LineRail], f);
            } else if (n == QLatin1String("transit")) {
                if (f.type == 2 && kind == QLatin1String("rail")) addLines(st->lines[LineRail], f);
            } else if (n == QLatin1String("boundaries")) {
                if (f.type != 2) continue;
                addLines(st->lines[kind == QLatin1String("country") ? LineBorderCountry : LineBorderRegion], f);
            } else if (n == QLatin1String("places")) {
                if (f.type != 1 || f.parts.isEmpty() || f.parts[0].isEmpty())
                    continue;
                const QString name = localName(f.props);
                if (name.isEmpty())
                    continue;
                MapLabel l;
                l.text = name;
                l.pos = f.parts[0][0];
                if (kind == QLatin1String("country")) { l.cls = LabelCountry; l.minZoom = 0; }
                else if (kind == QLatin1String("region")) { l.cls = LabelRegion; l.minZoom = 4; }
                else if (kind == QLatin1String("locality")) {
                    if (detail == QLatin1String("city")) { l.cls = LabelCity; l.minZoom = 4; }
                    else if (detail == QLatin1String("town")) { l.cls = LabelTown; l.minZoom = 7; }
                    else if (detail == QLatin1String("village")) { l.cls = LabelVillage; l.minZoom = 9; }
                    else { l.cls = LabelSmall; l.minZoom = 10.5; }
                } else if (kind == QLatin1String("neighbourhood") || kind == QLatin1String("macrohood")) { l.cls = LabelSmall; l.minZoom = 11; }
                else continue;
                const double mz = num(f.props.value(QStringLiteral("min_zoom")), -1);
                if (mz >= 0) l.minZoom = std::max(l.minZoom, mz - 1.0) ;
                l.rank = num(f.props.value(QStringLiteral("population_rank")), 0) * -1.0 + l.cls * 100.0 - num(f.props.value(QStringLiteral("population")), 0) / 1e9;
                st->labels.append(l);
            }
        }
    }
    return st;
}

std::unique_ptr<MapData> MapData::open(const QString &file, QString *error)
{
    auto a = PmTilesArchive::open(file, error);
    if (!a)
        return nullptr;
    if (!a->isVector()) {
        if (error) *error = QStringLiteral("Only vector (MVT) map packs can be drawn so far.");
        return nullptr;
    }
    std::unique_ptr<MapData> d(new MapData);
    d->m_archive = std::move(a);
    return d;
}

std::shared_ptr<StyledTile> MapData::tile(int z, int x, int y) const
{
    const quint64 key = (quint64(z) << 56) | (quint64(x) << 28) | quint64(y);
    {
        QMutexLocker l(&m_lock);
        if (auto *c = m_cache.object(key))
            return *c;
    }
    const QByteArray raw = m_archive->tile(z, x, y);
    std::shared_ptr<StyledTile> st;
    if (!raw.isEmpty()) {
        const MvtTile t = decodeMvt(raw);
        if (t.ok)
            st = styleTile(t);
    }
    QMutexLocker l(&m_lock);
    m_cache.insert(key, new std::shared_ptr<StyledTile>(st));
    return st;
}

void MapData::buildPlaceIndex(int maxZoom)
{
    const PmHeader &h = m_archive->header();
    QVector<Place> found;
    QSet<QString> seen;
    const int zTop = std::min<int>(maxZoom, h.maxZoom);
    for (int z = h.minZoom; z <= zTop; ++z) {
        const double n = std::pow(2.0, z);
        auto tx = [&](double lon) { return int(std::floor((lon + 180.0) / 360.0 * n)); };
        auto ty = [&](double lat) {
            const double s = std::sin(std::clamp(lat, -85.0511, 85.0511) * M_PI / 180.0);
            return int(std::floor((0.5 - std::log((1 + s) / (1 - s)) / (4 * M_PI)) * n));
        };
        const int x0 = std::max(0, tx(h.minLon)), x1 = std::min(int(n) - 1, tx(h.maxLon));
        const int y0 = std::max(0, ty(h.maxLat)), y1 = std::min(int(n) - 1, ty(h.minLat));
        for (int x = x0; x <= x1; ++x)
            for (int y = y0; y <= y1; ++y) {
                const QByteArray raw = m_archive->tile(z, x, y);
                if (raw.isEmpty())
                    continue;
                const MvtTile t = decodeMvt(raw);
                const MvtLayer *places = t.layer(QStringLiteral("places"));
                if (!places)
                    continue;
                for (const MvtFeature &f : places->features) {
                    const QString name = localName(f.props);
                    if (name.isEmpty() || f.parts.isEmpty() || f.parts[0].isEmpty())
                        continue;
                    const QString local = f.str("name");
                    const QString kind = f.str("kind"), detail = f.str("kind_detail");
                    if (kind != QLatin1String("locality") && kind != QLatin1String("country") && kind != QLatin1String("region")
                        && kind != QLatin1String("neighbourhood") && kind != QLatin1String("macrohood"))
                        continue;
                    const QPointF p = f.parts[0][0];
                    const double wx = (x + p.x() / places->extent) / n, wy = (y + p.y() / places->extent) / n;
                    Place pl;
                    pl.name = name;
                    pl.altName = local != name ? local : QString();
                    pl.kind = kind == QLatin1String("locality") && !detail.isEmpty() ? detail : kind;
                    pl.lon = wx * 360.0 - 180.0;
                    const double m = M_PI - 2.0 * M_PI * wy;
                    pl.lat = 180.0 / M_PI * std::atan(0.5 * (std::exp(m) - std::exp(-m)));
                    pl.rank = num(f.props.value(QStringLiteral("population_rank")), 0) * -1.0;
                    pl.zoom = pl.kind == QLatin1String("country") ? 5 : pl.kind == QLatin1String("region") ? 7 : pl.kind == QLatin1String("city") ? 11
                            : pl.kind == QLatin1String("town") ? 12 : 13;
                    const QString dk = name + QLatin1Char('|') + QString::number(std::round(pl.lon * 20)) + QLatin1Char('|') + QString::number(std::round(pl.lat * 20));
                    if (seen.contains(dk))
                        continue;
                    seen.insert(dk);
                    found.append(pl);
                }
            }
    }
    QMutexLocker l(&m_lock);
    m_places = found;
    m_placesReady = true;
}

QList<Place> MapData::searchPlaces(const QString &text, int limit) const
{
    const QString q = text.trimmed();
    QList<Place> starts, contains;
    if (q.isEmpty())
        return {};
    QMutexLocker l(&m_lock);
    for (const Place &p : m_places) {
        if (p.name.startsWith(q, Qt::CaseInsensitive) || p.altName.startsWith(q, Qt::CaseInsensitive)) starts.append(p);
        else if (p.name.contains(q, Qt::CaseInsensitive) || p.altName.contains(q, Qt::CaseInsensitive)) contains.append(p);
    }
    auto better = [](const Place &a, const Place &b) { return a.rank != b.rank ? a.rank < b.rank : a.name < b.name; };
    std::sort(starts.begin(), starts.end(), better);
    std::sort(contains.begin(), contains.end(), better);
    QList<Place> out = starts + contains;
    if (out.size() > limit)
        out.resize(limit);
    return out;
}

} // namespace Taos::Survival
