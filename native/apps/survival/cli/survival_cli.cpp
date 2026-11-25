// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-survival-cli: the engines of Survival Studio from a terminal, for scripts and for agents. Everything the window
// does with packs, the library, radios, kits and the map has a command here that prints JSON.
#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>
#include <QStorageInfo>
#include <QTextStream>
#include <iostream>

#include "articlehtml.h"
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
    QString e;
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
