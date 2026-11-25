// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Offline maps by region in the map engine's own format (PMTiles, Protomaps vector schema). Two routes: ready made
// files (the US state maps N.O.M.A.D.'s maps repository hosts, no checksum published) and extraction of any box out
// of the newest Protomaps build with the `pmtiles` tool, which fetches only the byte ranges it needs. The tool runs
// through ContainerExec, so it lives in the survival-studio container.
#include <QList>
#include <QObject>
#include <QProcess>
#include <QString>
#include <QUrl>

#include "containerexec.h"

namespace Taos::Survival {

struct MapRegion
{
    QString id, title, group;
    double west = 0, south = 0, east = 0, north = 0;
    qint64 bytes = 0;         // ready-made files: the size the catalogue gives; 0 for extraction
    QUrl url;                 // ready-made files only
    QString version;
    bool sizeApprox = false;
};

QList<MapRegion> loadMapRegions(const QByteArray &json, QString *error = nullptr);
/// pmtiles extract <source> <out> --bbox=w,s,e,n --maxzoom=z [--dry-run]
QStringList pmtilesExtractArgs(const QString &source, const QString &out, const MapRegion &r, int maxZoom, bool dryRun);
/// The newest build address out of build-metadata.protomaps.dev/builds.json.
QUrl latestProtomapsBuild(const QByteArray &buildsJson);
/// "Extract transferred 22 MB (overfetch 0.05) for an archive size of 21 MB" gives 21 MB in bytes; 0 when absent.
qint64 parseExtractSize(const QString &toolOutput);
int clampMaxZoom(int z);

class MapExtractor : public QObject
{
    Q_OBJECT
public:
    explicit MapExtractor(ContainerExec *exec, QObject *parent = nullptr) : QObject(parent), m_exec(exec) {}
    /// Runs the tool. dryRun only measures. `outFile` is written in place only when the tool succeeds.
    bool start(const QString &toolSource, const MapRegion &r, int maxZoom, const QString &outFile, bool dryRun, QString *error);
    void cancel();
    bool running() const { return m_proc != nullptr; }
Q_SIGNALS:
    void finished(bool ok, bool dryRun, qint64 bytes, const QString &message);
private:
    ContainerExec *m_exec;
    QProcess *m_proc = nullptr;
};

} // namespace Taos::Survival
