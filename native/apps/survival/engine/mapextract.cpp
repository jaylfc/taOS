// SPDX-License-Identifier: AGPL-3.0-or-later
#include "mapextract.h"

#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

namespace Taos::Survival {

int clampMaxZoom(int z) { return qBound(0, z, 15); }

QList<MapRegion> loadMapRegions(const QByteArray &json, QString *error)
{
    QList<MapRegion> out;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = QStringLiteral("The region list is not valid JSON.");
        return out;
    }
    for (const QJsonValue &v : d.object().value(QStringLiteral("regions")).toArray()) {
        const QJsonObject o = v.toObject();
        MapRegion r;
        r.id = o.value(QStringLiteral("id")).toString();
        r.title = o.value(QStringLiteral("title")).toString();
        r.group = o.value(QStringLiteral("group")).toString();
        r.west = o.value(QStringLiteral("west")).toDouble(); r.south = o.value(QStringLiteral("south")).toDouble();
        r.east = o.value(QStringLiteral("east")).toDouble(); r.north = o.value(QStringLiteral("north")).toDouble();
        r.bytes = qint64(o.value(QStringLiteral("bytes")).toDouble());
        r.url = QUrl(o.value(QStringLiteral("url")).toString());
        r.version = o.value(QStringLiteral("version")).toString();
        r.sizeApprox = o.value(QStringLiteral("sizeApprox")).toBool();
        const bool boxOk = !r.url.isEmpty() || (r.west >= -180 && r.east <= 180 && r.south >= -90 && r.north <= 90 && r.west < r.east && r.south < r.north);
        const bool readyOk = r.url.isEmpty() || (r.url.scheme() == QStringLiteral("https") && r.bytes > 0);
        if (r.id.isEmpty() || r.title.isEmpty() || !boxOk || !readyOk)
            continue;   // a malformed or non-https entry is skipped, not trusted
        out.append(r);
    }
    return out;
}

QStringList pmtilesExtractArgs(const QString &source, const QString &out, const MapRegion &r, int maxZoom, bool dryRun)
{
    QStringList a{QStringLiteral("extract"), source, out,
                  QStringLiteral("--bbox=%1,%2,%3,%4").arg(r.west, 0, 'f', 4).arg(r.south, 0, 'f', 4).arg(r.east, 0, 'f', 4).arg(r.north, 0, 'f', 4),
                  QStringLiteral("--maxzoom=%1").arg(clampMaxZoom(maxZoom))};
    if (dryRun)
        a << QStringLiteral("--dry-run");
    return a;
}

QUrl latestProtomapsBuild(const QByteArray &buildsJson)
{
    static const QRegularExpression keyRe(QStringLiteral("^\\d{8}\\.pmtiles$"));
    QString best;
    for (const QJsonValue &v : QJsonDocument::fromJson(buildsJson).array()) {
        const QString k = v.toObject().value(QStringLiteral("key")).toString();
        if (keyRe.match(k).hasMatch() && k > best)
            best = k;
    }
    return best.isEmpty() ? QUrl() : QUrl(QStringLiteral("https://build.protomaps.com/") + best);
}

qint64 parseExtractSize(const QString &out)
{
    static const QRegularExpression re(QStringLiteral("archive size of (\\d+(?:\\.\\d+)?) ?([KMGT]?B)"));
    const auto m = re.match(out);
    if (!m.hasMatch()) return 0;
    double v = m.captured(1).toDouble();
    const QString u = m.captured(2);
    const double mul = u == QStringLiteral("KB") ? 1e3 : u == QStringLiteral("MB") ? 1e6 : u == QStringLiteral("GB") ? 1e9 : u == QStringLiteral("TB") ? 1e12 : 1;
    return qint64(v * mul);
}

bool MapExtractor::start(const QString &source, const MapRegion &r, int maxZoom, const QString &outFile, bool dryRun, QString *error)
{
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    if (m_proc)
        return fail(QStringLiteral("An extraction is already running."));
    if (!source.startsWith(QStringLiteral("https://")) && !(source.startsWith(QStringLiteral("file://")) && qEnvironmentVariableIsSet("TAOS_SURVIVAL_ALLOW_FILE_URLS")))
        return fail(QStringLiteral("Map data is only fetched over https."));
    if (r.west >= r.east || r.south >= r.north)
        return fail(QStringLiteral("That map area is empty."));
    // the tool writes to a temporary name; only a finished, non-empty file is moved into place
    const QString tmp = outFile + QStringLiteral(".part");
    QFile::remove(tmp);
    m_proc = m_exec->makeProcess(QStringLiteral("pmtiles"), pmtilesExtractArgs(source, dryRun ? QStringLiteral("/dev/null") : tmp, r, maxZoom, dryRun), this);
    connect(m_proc, &QProcess::finished, this, [this, outFile, tmp, dryRun](int code, QProcess::ExitStatus st) {
        const QString out = QString::fromUtf8(m_proc->readAll());
        m_proc->deleteLater();
        m_proc = nullptr;
        if (st != QProcess::NormalExit || code != 0) {
            QFile::remove(tmp);
            Q_EMIT finished(false, dryRun, 0, QStringLiteral("The map tool stopped: %1").arg(out.right(300).simplified()));
            return;
        }
        if (dryRun) {
            const qint64 b = parseExtractSize(out);
            Q_EMIT finished(b > 0, true, b, b > 0 ? QString() : QStringLiteral("The map tool did not say how big the extract would be."));
            return;
        }
        const qint64 size = QFileInfo(tmp).size();
        if (size <= 0 || !QFile::rename(tmp, outFile)) {
            QFile::remove(tmp);
            Q_EMIT finished(false, false, 0, QStringLiteral("The map tool finished but left no usable file."));
            return;
        }
        Q_EMIT finished(true, false, size, QString());
    });
    connect(m_proc, &QProcess::errorOccurred, this, [this, tmp, dryRun](QProcess::ProcessError e) {
        if (e != QProcess::FailedToStart || !m_proc) return;
        m_proc->deleteLater();
        m_proc = nullptr;
        QFile::remove(tmp);
        Q_EMIT finished(false, dryRun, 0, QStringLiteral("The map tool (pmtiles) is not available. It lives in the survival-studio container."));
    });
    m_proc->start();
    return true;
}

void MapExtractor::cancel()
{
    if (m_proc)
        m_proc->kill();   // our own process; the .part file is removed when it reports finished
}

} // namespace Taos::Survival
