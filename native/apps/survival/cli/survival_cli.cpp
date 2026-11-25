// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-survival-cli: the engines of Survival Studio from a terminal, for scripts and for agents. Everything the window
// does with packs, the library, radios, kits and the map has a command here that prints JSON.
#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QTimer>
#include <QStandardPaths>
#include <QStorageInfo>
#include <QTextStream>
#include <iostream>

#include "articlehtml.h"
#include "containerexec.h"
#include "education.h"
#include "kiwixcatalog.h"
#include "mapextract.h"
#include "kit.h"
#include "mapdata.h"
#include "packmanager.h"
#include "paths.h"
#include "radiodetector.h"
#include "statestore.h"
#include "zimarchive.h"

using namespace Taos::Survival;

namespace {
QTextStream out(stdout);
QTextStream &out_ = out;
QTextStream err(stderr);
void print(const QJsonValue &v)
{
    const QJsonDocument d = v.isArray() ? QJsonDocument(v.toArray()) : QJsonDocument(v.toObject());
    out << QString::fromUtf8(d.toJson(QJsonDocument::Indented));
}
int usage()
{
    err << "usage: taos-survival-cli <command>\n"
           "  packs                        list the catalogue and what is installed\n"
           "  packs download <id>          download (resumes), verify, install\n"
           "  packs verify <id>            hash an installed pack again\n"
           "  packs remove <id>            delete a pack\n"
           "  packs import <file> [sha256] copy a .zim or .pmtiles file in\n"
           "  library info <pack>          what the pack says about itself\n  library search <pack> <text> title and full text search\n"
           "  library article <pack> <path> the cleaned article (HTML subset)\n"
           "  radios [--probe <device>]    serial devices that could be mesh radios; --probe runs the read-only handshake\n"
           "  kits                         grab-and-go kits checked against this machine\n"
           "  map info <pack>              map pack details\n"
           "  map search <pack> <text>     place names in a map pack\n  map tile <pack> <z> <x> <y>  what a tile holds (debugging a style)\n"
           "  catalog search <text> [lang] [category]   the whole Kiwix library (lang is ISO 639-3, e.g. eng)\n"
           "  catalog get <file name>      download one pack from the catalogue (e.g. wikipedia_en_medicine_mini_2026-10.zim)\n"
           "  maps regions                 areas that can be cut out; ready-made files\n"
           "  maps extract <area id> <zoom> [--dry-run]   cut a map out of the newest Protomaps build (runs pmtiles via TAOS_SURVIVAL_EXEC)\n"
           "  edu list                     Kolibri education channels\n"
           "  edu install <channel id>     import a channel into Kolibri (via TAOS_SURVIVAL_EXEC)\n"
           "  markers                      the saved markers\n"
           "  markers add <name> <lon> <lat> [note]\n"
           "  markers remove <id>\n"
           "The data folder is $TAOS_SURVIVAL_DIR or <data home>/taos/survival.\n";
    return 2;
}
QString packFile(PackManager &pm, const QString &idOrFile)
{
    if (QFileInfo::exists(idOrFile))
        return idOrFile;
    const PackInfo p = pm.pack(idOrFile);
    return p.installed ? p.path : QString();
}
} // namespace

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QStringList a = app.arguments().mid(1);
    if (a.isEmpty())
        return usage();
    PackManager pm(survivalDataDir());
    QString e, e2;
    pm.loadCatalogue(bundledCatalog(QStringLiteral("packs.json")), &e);
    const QString cmd = a[0];
    const QString sub = a.value(1);

    if (cmd == QLatin1String("packs")) {
        if (sub.isEmpty()) {
            QJsonArray arr;
            for (const PackInfo &p : pm.packs())
                arr.append(pm.toJson(p));
            print(arr);
            return 0;
        }
        if (sub == QLatin1String("download")) {
            const QString id = a.value(2);
            if (!pm.download(id, &e)) { err << e << "\n"; return 1; }
            QEventLoop loop;
            int rc = 1;
            QObject::connect(&pm, &PackManager::finished, &loop, [&](const QString &, bool ok, const QString &msg) { rc = ok ? 0 : 1; err << msg << "\n"; loop.quit(); });
            QObject::connect(&pm, &PackManager::progress, &loop, [](const QString &, qint64 got, qint64 total) {
                err << "\r" << got / (1024 * 1024) << " of " << total / (1024 * 1024) << " MB" << Qt::flush; });
            loop.exec();
            return rc;
        }
        if (sub == QLatin1String("verify")) {
            const VerifyResult v = pm.verifyInstalled(a.value(2));
            out << (v == VerifyResult::Ok ? "ok" : v == VerifyResult::Mismatch ? "mismatch" : v == VerifyResult::NoChecksum ? "no-checksum" : "unreadable") << "\n";
            return v == VerifyResult::Ok ? 0 : 1;
        }
        if (sub == QLatin1String("remove")) {
            if (!pm.remove(a.value(2), &e)) { err << e << "\n"; return 1; }
            return 0;
        }
        if (sub == QLatin1String("import")) {
            if (!pm.importFile(a.value(2), a.value(3), &e)) { err << e << "\n"; return 1; }
            return 0;
        }
        return usage();
    }
    if (cmd == QLatin1String("library")) {
        const QString file = packFile(pm, a.value(2));
        auto z = file.isEmpty() ? nullptr : ZimArchive::open(file, &e);
        if (!z) { err << "Cannot open that pack. " << e << "\n"; return 1; }
        if (sub == QLatin1String("info")) {
            const ZimInfo &i = z->info();
            print(QJsonObject{{QStringLiteral("title"), i.title}, {QStringLiteral("description"), i.description}, {QStringLiteral("language"), i.language},
                {QStringLiteral("creator"), i.creator}, {QStringLiteral("publisher"), i.publisher}, {QStringLiteral("date"), i.date}, {QStringLiteral("licence"), i.licence},
                {QStringLiteral("articles"), double(i.articleCount)}, {QStringLiteral("entries"), double(i.entryCount)}, {QStringLiteral("fullText"), i.hasFulltext},
                {QStringLiteral("titleIndex"), i.hasTitleIndex}, {QStringLiteral("main"), i.mainPath}});
            return 0;
        }
        if (sub == QLatin1String("search")) {
            QJsonArray arr;
            for (const ZimHit &h : z->search(a.value(3)))
                arr.append(QJsonObject{{QStringLiteral("path"), h.path}, {QStringLiteral("title"), h.title}, {QStringLiteral("snippet"), h.snippet}});
            print(arr);
            return 0;
        }
        if (sub == QLatin1String("article")) {
            const ZimEntry en = z->entry(a.value(3));
            if (!en.found) { err << "No such article.\n"; return 1; }
            if (!en.mimeType.startsWith(QStringLiteral("text/html"))) { err << "Not an HTML article (" << en.mimeType << ").\n"; return 1; }
            const SanitizedArticle s = sanitizeArticle(en.data, a.value(2), en.path);
            out << s.html << "\n";
            return 0;
        }
        return usage();
    }
    if (cmd == QLatin1String("radios")) {
        RadioDetector det;
        QList<RadioCandidate> list = det.scan();
        if (sub == QLatin1String("--probe")) {
            for (RadioCandidate &c : list)
                if (c.device == a.value(2)) {
                    const ProbeResult r = det.probe(c);
                    c.probed = true; c.protocol = r.protocol; c.firmware = r.firmware; c.probeNote = r.note;
                }
        }
        QJsonArray arr;
        for (const RadioCandidate &c : list)
            arr.append(QJsonObject{{QStringLiteral("device"), c.device}, {QStringLiteral("vid"), QStringLiteral("%1").arg(c.vid, 4, 16, QLatin1Char('0'))},
                {QStringLiteral("pid"), QStringLiteral("%1").arg(c.pid, 4, 16, QLatin1Char('0'))}, {QStringLiteral("product"), c.product},
                {QStringLiteral("chip"), c.chip}, {QStringLiteral("confidence"), c.confidence}, {QStringLiteral("inUse"), c.inUse},
                {QStringLiteral("probed"), c.probed}, {QStringLiteral("protocol"), c.protocol}, {QStringLiteral("note"), c.probeNote}});
        print(arr);
        return 0;
    }
    if (cmd == QLatin1String("kits")) {
        KitContext ctx;
        for (const PackInfo &p : pm.packs()) {
            if (!p.installed) continue;
            (p.kind == QLatin1String("zim") ? ctx.zimPacks : ctx.mapPacks)++;
            ctx.packBytes += p.installedBytes;
        }
        ctx.radiosPossible = int(RadioDetector().scan().size());
        ctx.freeBytes = QStorageInfo(survivalDataDir()).bytesAvailable();
        ctx.arch = QSysInfo::currentCpuArchitecture();
        print(evaluateKits(QJsonDocument::fromJson(bundledCatalog(QStringLiteral("kits.json"))).object(), ctx));
        return 0;
    }
    if (cmd == QLatin1String("map")) {
        const QString file = packFile(pm, a.value(2));
        auto m = file.isEmpty() ? nullptr : MapData::open(file, &e);
        if (!m) { err << "Cannot open that map. " << e << "\n"; return 1; }
        if (sub == QLatin1String("info")) {
            const PmHeader &h = m->archive().header();
            print(QJsonObject{{QStringLiteral("name"), m->archive().name()}, {QStringLiteral("attribution"), m->archive().attribution()},
                {QStringLiteral("minZoom"), h.minZoom}, {QStringLiteral("maxZoom"), h.maxZoom}, {QStringLiteral("minLon"), h.minLon},
                {QStringLiteral("minLat"), h.minLat}, {QStringLiteral("maxLon"), h.maxLon}, {QStringLiteral("maxLat"), h.maxLat}});
            return 0;
        }
        if (sub == QLatin1String("tile")) {   // layers, feature counts and one example feature of each: for debugging a style
            const MvtTile t = decodeMvt(m->archive().tile(a.value(3).toInt(), a.value(4).toInt(), a.value(5).toInt()));
            QJsonArray arr;
            for (const MvtLayer &l : t.layers) {
                QJsonObject o{{QStringLiteral("layer"), l.name}, {QStringLiteral("features"), l.features.size()}, {QStringLiteral("extent"), l.extent}};
                if (!l.features.isEmpty()) o.insert(QStringLiteral("example"), QJsonObject::fromVariantMap(l.features.first().props));
                arr.append(o);
            }
            print(arr);
            return 0;
        }
        if (sub == QLatin1String("search")) {
            m->buildPlaceIndex();
            QJsonArray arr;
            for (const Place &p : m->searchPlaces(a.value(3)))
                arr.append(QJsonObject{{QStringLiteral("name"), p.name}, {QStringLiteral("kind"), p.kind}, {QStringLiteral("lon"), p.lon}, {QStringLiteral("lat"), p.lat}});
            print(arr);
            return 0;
        }
        return usage();
    }
    if (cmd == QLatin1String("catalog")) {
        KiwixCatalog cat;
        QEventLoop loop;
        int rc = 0;
        CatalogPage page;
        QObject::connect(&cat, &KiwixCatalog::searchDone, &loop, [&](int, const CatalogPage &p) { page = p; loop.quit(); });
        QObject::connect(&cat, &KiwixCatalog::searchFailed, &loop, [&](int, const QString &m) { err << m << "\n"; rc = 1; loop.quit(); });
        if (sub == QLatin1String("search")) {
            cat.search(a.value(2), a.value(3), a.value(4), 0, 50);
            loop.exec();
            if (rc) return rc;
            QJsonArray arr;
            for (const CatalogEntry &e : std::as_const(page.entries)) {
                const PackInfo p = packFromEntry(e);
                arr.append(QJsonObject{{QStringLiteral("file"), e.fileName()}, {QStringLiteral("title"), e.title}, {QStringLiteral("flavour"), e.flavour},
                    {QStringLiteral("language"), e.language}, {QStringLiteral("category"), e.category}, {QStringLiteral("bytes"), double(e.bytes)},
                    {QStringLiteral("licence"), p.licence}, {QStringLiteral("restrictive"), p.needsAcceptance}, {QStringLiteral("version"), e.version()}});
            }
            print(QJsonObject{{QStringLiteral("total"), page.total}, {QStringLiteral("entries"), arr}});
            return 0;
        }
        if (sub == QLatin1String("get")) {
            const QString file = a.value(2);
            cat.search(QString(), QString(), QString(), 0, 100, catalogNameOfSeries(seriesOfFile(file)));
            loop.exec();
            if (rc) return rc;
            for (const CatalogEntry &e : std::as_const(page.entries)) {
                if (e.fileName() != file) continue;
                PackInfo p = packFromEntry(e);
                pm.addRemote(p);
                if (p.needsAcceptance && !a.contains(QStringLiteral("--accept-licence"))) {
                    err << "This pack is under " << p.licence << ", which restricts use. Read it, then run again with --accept-licence.\n";
                    return 1;
                }
                if (p.needsAcceptance) pm.acceptLicence(p.id);
                if (!pm.download(p.id, &e2)) { err << e2 << "\n"; return 1; }
                QEventLoop dl;
                int drc = 1;
                QObject::connect(&pm, &PackManager::finished, &dl, [&](const QString &, bool ok, const QString &msg) { drc = ok ? 0 : 1; err << msg << "\n"; dl.quit(); });
                QObject::connect(&pm, &PackManager::progress, &dl, [](const QString &, qint64 got, qint64 total) {
                    err << "\r" << got / (1024 * 1024) << " of " << total / (1024 * 1024) << " MB" << Qt::flush; });
                dl.exec();
                return drc;
            }
            err << "That file is not in the catalogue.\n";
            return 1;
        }
        return usage();
    }
    if (cmd == QLatin1String("maps")) {
        const QList<MapRegion> regions = loadMapRegions(bundledCatalog(QStringLiteral("regions.json")));
        if (sub == QLatin1String("regions")) {
            QJsonArray arr;
            for (const MapRegion &r : regions)
                arr.append(QJsonObject{{QStringLiteral("id"), r.id}, {QStringLiteral("title"), r.title}, {QStringLiteral("group"), r.group},
                    {QStringLiteral("readyMade"), !r.url.isEmpty()}, {QStringLiteral("bytes"), double(r.bytes)}});
            print(arr);
            return 0;
        }
        if (sub == QLatin1String("extract")) {
            for (const MapRegion &r : regions) {
                if (r.id != a.value(2) || !r.url.isEmpty()) continue;
                QNetworkAccessManager nam;
                QEventLoop loop;
                QNetworkReply *rep = nam.get(QNetworkRequest(QUrl(QStringLiteral("https://build-metadata.protomaps.dev/builds.json"))));
                QObject::connect(rep, &QNetworkReply::finished, &loop, &QEventLoop::quit);
                loop.exec();
                const QUrl build = latestProtomapsBuild(rep->read(4 * 1024 * 1024));
                if (build.isEmpty()) { err << "The newest map build could not be found.\n"; return 1; }
                ContainerExec exec;
                MapExtractor ex(&exec);
                const bool dry = a.contains(QStringLiteral("--dry-run"));
                const QString out = pm.packsDir() + QStringLiteral("/protomaps-") + r.id + QStringLiteral("-z") + a.value(3) + QStringLiteral("-") + build.fileName().chopped(8) + QStringLiteral(".pmtiles");
                int rc = 1;
                QObject::connect(&ex, &MapExtractor::finished, &loop, [&](bool ok, bool, qint64 bytes, const QString &msg) {
                    rc = ok ? 0 : 1;
                    if (!ok) err << msg << "\n"; else out_ << bytes << "\n";
                    loop.quit();
                });
                if (!ex.start(build.toString(), r, a.value(3).toInt(), out, dry, &e)) { err << e << "\n"; return 1; }
                loop.exec();
                if (rc == 0 && !dry) {
                    PackInfo p; p.id = QStringLiteral("map:extract-") + r.id + QStringLiteral("-z") + a.value(3); p.title = r.title; p.kind = QStringLiteral("map");
                    p.file = QFileInfo(out).fileName(); p.licence = QStringLiteral("ODbL-1.0"); p.series = QStringLiteral("protomaps-") + r.id;
                    p.attribution = QStringLiteral("Map data (c) OpenStreetMap contributors (ODbL). Basemap by Protomaps.");
                    if (!pm.adoptFile(p, &e)) { err << e << "\n"; return 1; }
                }
                return rc;
            }
            err << "Unknown area. List them with: maps regions\n";
            return 1;
        }
        return usage();
    }
    if (cmd == QLatin1String("edu")) {
        if (sub == QLatin1String("list")) {
            QNetworkAccessManager nam;
            QEventLoop loop;
            QNetworkReply *rep = nam.get(QNetworkRequest(QUrl(QStringLiteral("https://kolibri-catalog-en.learningequality.org/api/public/v1/channels/?format=json"))));
            QObject::connect(rep, &QNetworkReply::finished, &loop, &QEventLoop::quit);
            loop.exec();
            QJsonArray arr;
            for (const EduChannel &c : parseKolibriChannels(rep->read(32 * 1024 * 1024)))
                arr.append(QJsonObject{{QStringLiteral("id"), c.id}, {QStringLiteral("name"), c.name}, {QStringLiteral("language"), c.language},
                    {QStringLiteral("bytes"), double(c.bytes)}, {QStringLiteral("resources"), c.resources}});
            print(arr);
            return 0;
        }
        if (sub == QLatin1String("install")) {
            ContainerExec exec;
            ChannelImporter imp(&exec);
            QEventLoop loop;
            int rc = 1;
            QObject::connect(&imp, &ChannelImporter::finished, &loop, [&](const QString &, bool ok, const QString &msg) { rc = ok ? 0 : 1; if (!ok) err << msg << "\n"; loop.quit(); });
            if (!imp.start(a.value(2), &e)) { err << e << "\n"; return 1; }
            loop.exec();
            return rc;
        }
        return usage();
    }
    if (cmd == QLatin1String("markers")) {
        StateStore st(survivalDataDir());
        if (!st.load()) { err << "The saved state is damaged; it was not changed.\n"; return 1; }
        if (sub == QLatin1String("add")) {
            out << st.addMarker(a.value(2), a.value(5), a.value(3).toDouble(), a.value(4).toDouble()) << "\n";
            if (!st.save(&e)) { err << e << "\n"; return 1; }
            return 0;
        }
        if (sub == QLatin1String("remove")) {
            if (!st.removeMarker(a.value(2))) { err << "No such marker.\n"; return 1; }
            if (!st.save(&e)) { err << e << "\n"; return 1; }
            return 0;
        }
        print(st.markers());
        return 0;
    }
    return usage();
}
