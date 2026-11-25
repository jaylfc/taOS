// SPDX-License-Identifier: AGPL-3.0-or-later
// Reaching every pack source: the Kiwix OPDS catalogue (parse, https only, licences, checksums from the publisher,
// updates), regional maps (ready files and extraction through the container tool) and education channels (Kolibri,
// imported through the container). The tools are replaced by small shell scripts, so nothing is downloaded.
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "containerexec.h"
#include "education.h"
#include "kiwixcatalog.h"
#include "mapextract.h"
#include "packmanager.h"

using namespace Taos::Survival;
using namespace Qt::StringLiterals;

namespace {
void writeFile(const QString &p, const QByteArray &d, bool exec = false)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    QVERIFY2(f.open(QIODevice::WriteOnly), qPrintable(p));
    f.write(d);
    f.close();
    if (exec) f.setPermissions(f.permissions() | QFileDevice::ExeOwner);
}
QByteArray readData(const char *name)
{
    QFile f(QStringLiteral(SURVIVAL_TEST_DATA "/") + QLatin1String(name));
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}
}

class TstSurvivalPacks : public QObject
{
    Q_OBJECT
    QTemporaryDir m_dir;
    QString path(const QString &r) const { return m_dir.filePath(r); }

private Q_SLOTS:
    void initTestCase() { QVERIFY(m_dir.isValid()); qputenv("TAOS_SURVIVAL_ALLOW_FILE_URLS", "1"); }

    // ---- the catalogue ------------------------------------------------------------------------------------------
    void opds_entries_are_parsed_and_only_https_zims_kept()
    {
        const CatalogPage p = parseCatalogEntries(readData("feed.xml"));
        QCOMPARE(p.total, 3682);
        QCOMPARE(p.start, 30);
        QCOMPARE(p.entries.size(), 2);
        QCOMPARE(p.dropped, 2);   // the http link and the entry with no link
        const CatalogEntry &e = p.entries[0];
        QCOMPARE(e.name, u"wikipedia_en_all"_s);
        QCOMPARE(e.flavour, u"nopic"_s);
        QCOMPARE(e.language, u"eng"_s);
        QCOMPARE(e.bytes, qint64(52690707456));
        QCOMPARE(e.zimUrl.toString(), u"https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2026-06.zim"_s);   // .meta4 removed
        QCOMPARE(e.fileName(), u"wikipedia_en_all_nopic_2026-06.zim"_s);
        QCOMPARE(e.series(), u"wikipedia_en_all_nopic"_s);
        QCOMPARE(e.version(), u"2026-06"_s);
        QVERIFY(e.fullText());
        QVERIFY(!e.pictures());
        QCOMPARE(e.articles, qint64(19191219));
        QCOMPARE(e.publisher, u"openZIM"_s);
        for (const CatalogEntry &x : p.entries)
            QCOMPARE(x.zimUrl.scheme(), u"https"_s);
        QVERIFY(parseCatalogEntries("not xml at all").entries.isEmpty());
        QVERIFY(parseCatalogEntries("").entries.isEmpty());
    }
    void opds_facets_and_urls()
    {
        const QByteArray cats = "<feed><entry><title>education</title><link href=\"/catalog/v2/entries?category=education\"/></entry>"
                                "<entry><title>iFixit</title><link href=\"/catalog/v2/entries?category=iFixit\"/></entry></feed>";
        const auto c = parseCatalogFacets(cats);
        QCOMPARE(c.size(), 2);
        QCOMPARE(c[1].id, u"iFixit"_s);
        const QByteArray langs = "<feed><entry><title>Afrikaans</title><language>afr</language></entry></feed>";
        QCOMPARE(parseCatalogFacets(langs).first().id, u"afr"_s);
        const QUrl u = catalogEntriesUrl(QUrl(u"https://opds.library.kiwix.org/catalog/v2"_s), u"water purification"_s, u"eng"_s, u"other"_s, QString(), 60, 30);
        QCOMPARE(u.path(), u"/catalog/v2/entries"_s);
        QVERIFY(u.query(QUrl::FullyEncoded).contains(u"q=water%20purification"_s) || u.query(QUrl::FullyEncoded).contains(u"q=water+purification"_s));
        QVERIFY(u.query().contains(u"lang=eng"_s) && u.query().contains(u"category=other"_s) && u.query().contains(u"start=60"_s));
        QVERIFY(catalogEntriesUrl(QUrl(u"https://x/catalog/v2"_s), QString(), QString(), QString(), QString(), 0, 5000).query().contains(u"count=100"_s));   // clamped
    }
    void series_names_and_versions()
    {
        QCOMPARE(seriesOfFile(u"wikipedia_en_all_nopic_2026-06.zim"_s), u"wikipedia_en_all_nopic"_s);
        QCOMPARE(versionOfFile(u"wikipedia_en_all_nopic_2026-06.zim"_s), u"2026-06"_s);
        QCOMPARE(versionOfFile(u"nodate.zim"_s), QString());
        QCOMPARE(catalogNameOfSeries(u"wikipedia_en_all_nopic"_s), u"wikipedia_en_all"_s);
        QCOMPARE(catalogNameOfSeries(u"zimgit-water_en"_s), u"zimgit-water_en"_s);
    }
    void licences_free_restrictive_unknown()
    {
        QVERIFY(!classifyLicence(u"wikipedia_en_all_nopic"_s, u"wikipedia"_s).restrictive);
        QCOMPARE(classifyLicence(u"wikipedia_en_all_nopic"_s, QString()).licence, u"CC-BY-SA-4.0"_s);
        QVERIFY(!classifyLicence(u"gardening.stackexchange.com_en_all"_s, QString()).restrictive);
        QVERIFY(!classifyLicence(u"gutenberg_en_all"_s, u"gutenberg"_s).restrictive);
        for (const QString &s : {u"libretexts.org_en_math"_s, u"ifixit_en_all"_s, u"ted_mul_ted-ed"_s, u"100r.co_en_all"_s}) {
            const LicenceInfo l = classifyLicence(s, QString());
            QVERIFY2(l.restrictive && l.known, qPrintable(s));
        }
        const LicenceInfo u = classifyLicence(u"some-unknown-series_en"_s, QString());
        QVERIFY(u.restrictive);    // unknown means "read it first", never "free"
        QVERIFY(!u.known);
    }
    void entry_becomes_a_pack_with_the_publishers_checksum_url()
    {
        const CatalogPage p = parseCatalogEntries(readData("feed.xml"));
        const PackInfo a = packFromEntry(p.entries[0]);
        QCOMPARE(a.id, u"kiwix:wikipedia_en_all_nopic_2026-06"_s);
        QCOMPARE(a.sha256Url.toString(), a.url.toString() + u".sha256"_s);
        QVERIFY(a.sha256.isEmpty());
        QVERIFY(!a.needsAcceptance);
        QCOMPARE(a.series, u"wikipedia_en_all_nopic"_s);
        QCOMPARE(a.bytes, qint64(52690707456));
        const PackInfo b = packFromEntry(p.entries[1]);
        QVERIFY(b.needsAcceptance);
        QCOMPARE(b.licence, u"CC-BY-NC-SA-4.0"_s);
    }

    // ---- downloading from the catalogue ------------------------------------------------------------------------
private:
    PackInfo servedPack(const QString &series, const QString &version, const QByteArray &content, bool goodSum = true, bool restrictive = false)
    {
        const QString file = series + u"_"_s + version + u".zim"_s;
        const QString src = path(u"srv/"_s + file);
        writeFile(src, content);
        writeFile(src + u".sha256"_s, (goodSum ? PackManager::sha256Of(src).toLatin1() : QByteArray(64, 'a')) + "  " + file.toUtf8() + "\n");
        PackInfo p;
        p.id = u"kiwix:"_s + file.left(file.size() - 4);
        p.title = series;
        p.kind = u"zim"_s;
        p.file = file;
        p.url = QUrl::fromLocalFile(src);
        p.sha256Url = QUrl::fromLocalFile(src + u".sha256"_s);
        p.bytes = content.size();
        p.series = series;
        p.version = version;
        p.licence = u"CC-BY-SA-4.0"_s;
        p.needsAcceptance = restrictive;
        p.source = u"download.kiwix.org"_s;
        return p;
    }
private Q_SLOTS:
    void download_reads_the_publishers_checksum_then_verifies()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const PackInfo p = servedPack(u"series_a"_s, u"2026-01"_s, QByteArray(5000, 'a'));
        pm.addRemote(p);
        QSignalSpy done(&pm, &PackManager::finished);
        QString err;
        QVERIFY2(pm.download(p.id, &err), qPrintable(err));
        QVERIFY(done.wait(8000));
        QVERIFY(done.first().at(1).toBool());
        PackInfo i = pm.pack(p.id);
        QVERIFY(i.installed);
        QCOMPARE(pm.verifyInstalled(p.id), VerifyResult::Ok);
        // what it was is remembered across a restart: a new manager reads the record
        PackManager again(d.path());
        const PackInfo r = again.pack(p.id);
        QVERIFY(r.installed);
        QCOMPARE(r.series, u"series_a"_s);
        QCOMPARE(r.licence, u"CC-BY-SA-4.0"_s);
        QCOMPARE(r.version, u"2026-01"_s);
        QCOMPARE(r.source, u"download.kiwix.org"_s);
    }
    void a_wrong_publisher_checksum_deletes_the_download()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const PackInfo p = servedPack(u"series_b"_s, u"2026-01"_s, QByteArray(5000, 'b'), false);
        pm.addRemote(p);
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(p.id));
        QVERIFY(done.wait(8000));
        QVERIFY(!done.first().at(1).toBool());
        QVERIFY(!pm.pack(p.id).installed);
        QVERIFY(!QFile::exists(pm.packsDir() + u"/"_s + p.file + u".part"_s));
    }
    void an_unreadable_checksum_means_no_download_at_all()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        PackInfo p = servedPack(u"series_c"_s, u"2026-01"_s, QByteArray(5000, 'c'));
        QFile::remove(path(u"srv/series_c_2026-01.zim.sha256"_s));
        pm.addRemote(p);
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(p.id));
        QVERIFY(done.wait(8000));
        QVERIFY(!done.first().at(1).toBool());
        QVERIFY(done.first().at(2).toString().contains(u"checksum"_s));
        QVERIFY(!QFile::exists(pm.packsDir() + u"/"_s + p.file + u".part"_s));
        QVERIFY(!pm.pack(p.id).installed);
    }
    void restrictive_licences_need_acceptance_first()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const PackInfo p = servedPack(u"series_d"_s, u"2026-01"_s, QByteArray(3000, 'd'), true, true);
        pm.addRemote(p);
        QString err;
        QVERIFY(!pm.download(p.id, &err));
        QVERIFY(err.contains(u"licence"_s));
        pm.acceptLicence(p.id);
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(p.id, &err));
        QVERIFY(done.wait(8000));
        QVERIFY(done.first().at(1).toBool());
    }
    void a_pack_without_a_published_checksum_is_size_checked_and_says_so()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        PackInfo p = servedPack(u"series_e"_s, u"2026-01"_s, QByteArray(4000, 'e'));
        p.sha256Url = QUrl();
        p.noChecksum = true;
        pm.addRemote(p);
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(p.id));
        QVERIFY(done.wait(8000));
        QVERIFY(done.first().at(1).toBool());
        QVERIFY(pm.pack(p.id).unverified);
        // the file differs in size from the catalogue: refused
        PackInfo q = servedPack(u"series_f"_s, u"2026-01"_s, QByteArray(4000, 'f'));
        q.sha256Url = QUrl(); q.noChecksum = true; q.bytes = 3990;
        pm.addRemote(q);
        QSignalSpy done2(&pm, &PackManager::finished);
        QVERIFY(pm.download(q.id));
        QVERIFY(done2.wait(8000));
        QVERIFY(!done2.first().at(1).toBool());
        // and without the flag a missing checksum is still a refusal
        PackInfo r = servedPack(u"series_g"_s, u"2026-01"_s, QByteArray(10, 'g'));
        r.sha256Url = QUrl();
        pm.addRemote(r);
        QString err;
        QVERIFY(!pm.download(r.id, &err));
    }
    void an_approximate_size_that_cannot_be_confirmed_stops_the_download()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        PackInfo p = servedPack(u"series_h"_s, u"2026-01"_s, QByteArray(4000, 'h'));
        p.sizeApprox = true;   // the publisher cannot be asked (nothing listens there), so the size stays unconfirmed
        p.url = QUrl(u"https://127.0.0.1:9/series_h_2026-01.zim"_s);
        pm.addRemote(p);
        QSignalSpy done(&pm, &PackManager::finished);
        QString derr;
        QVERIFY2(pm.download(p.id, &derr), qPrintable(derr));
        QVERIFY(done.wait(8000));
        QVERIFY(!done.first().at(1).toBool());
        QVERIFY(!QFile::exists(pm.packsDir() + u"/"_s + p.file + u".part"_s));
    }
    void updates_are_found_by_series_and_the_old_file_goes_after_the_new_one_verifies()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const PackInfo v1 = servedPack(u"series_u"_s, u"2026-01"_s, QByteArray(4000, '1'));
        pm.addRemote(v1);
        QSignalSpy d1(&pm, &PackManager::finished);
        QVERIFY(pm.download(v1.id));
        QVERIFY(d1.wait(8000));
        QVERIFY(!pm.pack(v1.id).updateAvailable);
        const PackInfo v2 = servedPack(u"series_u"_s, u"2026-03"_s, QByteArray(4500, '2'));
        const PackInfo older = servedPack(u"series_u"_s, u"2025-12"_s, QByteArray(10, '0'));
        const PackInfo other = servedPack(u"series_other"_s, u"2027-01"_s, QByteArray(10, 'x'));
        pm.setLatest({older, other});
        QVERIFY(!pm.pack(v1.id).updateAvailable);   // an older version, and another series, are not updates
        pm.setLatest({older, other, v2});
        const PackInfo i = pm.pack(v1.id);
        QVERIFY(i.updateAvailable);
        QCOMPARE(i.updateVersion, u"2026-03"_s);
        QSignalSpy d2(&pm, &PackManager::finished);
        QString err;
        QVERIFY2(pm.update(v1.id, &err), qPrintable(err));
        QVERIFY(QFile::exists(pm.packsDir() + u"/"_s + v1.file));   // still there while the new one downloads
        QVERIFY(d2.wait(8000));
        QVERIFY(d2.first().at(1).toBool());
        QVERIFY(pm.pack(v2.id).installed);
        QVERIFY(!QFile::exists(pm.packsDir() + u"/"_s + v1.file));
        QVERIFY(!QFile::exists(pm.packsDir() + u"/"_s + v1.file + u".taospack.json"_s));
        QVERIFY(!pm.update(v1.id, &err));   // nothing left to update
    }
    void a_failed_update_keeps_the_old_pack()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const PackInfo v1 = servedPack(u"series_k"_s, u"2026-01"_s, QByteArray(4000, '1'));
        pm.addRemote(v1);
        QSignalSpy d1(&pm, &PackManager::finished);
        QVERIFY(pm.download(v1.id));
        QVERIFY(d1.wait(8000));
        const PackInfo v2 = servedPack(u"series_k"_s, u"2026-03"_s, QByteArray(4500, '2'), false);   // wrong checksum
        pm.setLatest({v2});
        QSignalSpy d2(&pm, &PackManager::finished);
        QVERIFY(pm.update(v1.id));
        QVERIFY(d2.wait(8000));
        QVERIFY(!d2.first().at(1).toBool());
        QVERIFY(pm.pack(v1.id).installed);
        QVERIFY(QFile::exists(pm.packsDir() + u"/"_s + v1.file));
    }

    // ---- maps -------------------------------------------------------------------------------------------------
    void regions_load_and_bad_entries_are_skipped()
    {
        const QByteArray j = readData("regions-sample.json");
        const auto r = loadMapRegions(j);
        QStringList ids;
        for (const auto &x : r) ids << x.id;
        QCOMPARE(ids, QStringList({u"a"_s, u"d"_s}));
        QVERIFY(r[1].sizeApprox);
        QFile f(QStringLiteral(SURVIVAL_CATALOG_DIR "/regions.json"));
        QVERIFY(f.open(QIODevice::ReadOnly));
        QVERIFY(loadMapRegions(f.readAll()).size() > 90);   // world + presets + the 59 ready files
    }
    void extract_arguments_and_builds()
    {
        MapRegion r; r.west = -8.7; r.south = 49.8; r.east = 1.9; r.north = 60.95;
        const QStringList a = pmtilesExtractArgs(u"https://build.protomaps.com/20261120.pmtiles"_s, u"/o/gb.pmtiles"_s, r, 99, true);
        QCOMPARE(a[0], u"extract"_s);
        QVERIFY(a.contains(u"--bbox=-8.7000,49.8000,1.9000,60.9500"_s));
        QVERIFY(a.contains(u"--maxzoom=15"_s));   // clamped
        QVERIFY(a.contains(u"--dry-run"_s));
        QVERIFY(!pmtilesExtractArgs(u"s"_s, u"o"_s, r, 5, false).contains(u"--dry-run"_s));
        const QByteArray builds = readData("builds.json");
        QCOMPARE(latestProtomapsBuild(builds).toString(), u"https://build.protomaps.com/20261120.pmtiles"_s);
        QVERIFY(latestProtomapsBuild("[]").isEmpty());
        QCOMPARE(parseExtractSize(u"x\nExtract transferred 64 MB (overfetch 0.05) for an archive size of 62 MB"_s), qint64(62000000));
        QCOMPARE(parseExtractSize(u"nothing"_s), qint64(0));
    }
    void container_prefix_is_put_in_front_of_the_tool()
    {
        QCOMPARE(ContainerExec(QString()).command(u"pmtiles"_s, {u"x"_s}), QStringList({u"pmtiles"_s, u"x"_s}));
        QCOMPARE(ContainerExec(u"podman exec survival-studio"_s).command(u"kolibri"_s, {u"manage"_s}),
                 QStringList({u"podman"_s, u"exec"_s, u"survival-studio"_s, u"kolibri"_s, u"manage"_s}));
    }
    void extraction_runs_the_tool_and_keeps_only_a_finished_file()
    {
        // the "container tool": args are  pmtiles extract <source> <out> --bbox=... --maxzoom=N [--dry-run]
        const QString tool = path(u"fake-tool.sh"_s);
        writeFile(tool, "#!/bin/sh\nshift 2\nsrc=$1; out=$2\ncase \"$*\" in *--dry-run*) echo 'Extract transferred 22 MB (overfetch 0.05) for an archive size of 21 MB'; exit 0;; esac\n"
                        "case \"$src\" in *fail*) echo boom >&2; exit 3;; esac\nprintf 'PMTilesdata' > \"$out\"\n", true);
        ContainerExec exec(u"sh "_s + tool);
        MapExtractor ex(&exec);
        MapRegion r; r.id = u"x"_s; r.west = -5; r.south = 50; r.east = 2; r.north = 58;
        QString err;
        // not https: refused before anything runs
        QVERIFY(!ex.start(u"http://example.org/b.pmtiles"_s, r, 8, path(u"m/out.pmtiles"_s), false, &err));
        QVERIFY(err.contains(u"https"_s));
        QSignalSpy sp(&ex, &MapExtractor::finished);
        QVERIFY(ex.start(u"https://build.protomaps.com/20261120.pmtiles"_s, r, 8, path(u"m/dry.pmtiles"_s), true, &err));
        QVERIFY(sp.wait(8000));
        QVERIFY(sp.first().at(0).toBool());
        QCOMPARE(sp.first().at(2).toLongLong(), qint64(21000000));
        QVERIFY(!QFile::exists(path(u"m/dry.pmtiles"_s)));
        QDir().mkpath(path(u"m"_s));
        sp.clear();
        QVERIFY(ex.start(u"https://build.protomaps.com/20261120.pmtiles"_s, r, 8, path(u"m/out.pmtiles"_s), false, &err));
        QVERIFY(sp.wait(8000));
        QVERIFY(sp.first().at(0).toBool());
        QVERIFY(QFile::exists(path(u"m/out.pmtiles"_s)));
        QVERIFY(!QFile::exists(path(u"m/out.pmtiles.part"_s)));
        sp.clear();
        QVERIFY(ex.start(u"https://build.protomaps.com/fail.pmtiles"_s, r, 8, path(u"m/bad.pmtiles"_s), false, &err));
        QVERIFY(sp.wait(8000));
        QVERIFY(!sp.first().at(0).toBool());
        QVERIFY(!QFile::exists(path(u"m/bad.pmtiles"_s)));
        QVERIFY(!QFile::exists(path(u"m/bad.pmtiles.part"_s)));
        // the tool is not there
        ContainerExec none(u"/nonexistent/prefix"_s);
        MapExtractor ex2(&none);
        QSignalSpy sp2(&ex2, &MapExtractor::finished);
        QVERIFY(ex2.start(u"https://build.protomaps.com/20261120.pmtiles"_s, r, 8, path(u"m/n.pmtiles"_s), false, &err));
        QVERIFY(sp2.wait(8000));
        QVERIFY(!sp2.first().at(0).toBool());
        QVERIFY(sp2.first().at(3).toString().contains(u"not available"_s));
    }

    // ---- education --------------------------------------------------------------------------------------------
    void kolibri_channels_are_parsed_and_bad_ids_dropped()
    {
        const QByteArray j = readData("kolibri-channels.json");
        const auto c = parseKolibriChannels(j);
        QCOMPARE(c.size(), 1);
        QCOMPARE(c[0].resources, 27722);
        QCOMPARE(c[0].bytes, qint64(66859513620));
        QCOMPARE(c[0].lastPublished, u"2025-05-01"_s);
        QVERIFY(validChannelId(u"c9d7f950ab6b5a1199e3d6c10d7f0103"_s));
        QVERIFY(!validChannelId(u"C9D7F950AB6B5A1199E3D6C10D7F0103"_s));
        QVERIFY(!validChannelId(u"$(reboot)"_s));
    }
    void channel_import_runs_both_kolibri_steps_in_order()
    {
        const QString log = path(u"kolibri.log"_s), tool = path(u"fake-kolibri.sh"_s);
        QFile::remove(log);
        writeFile(tool, "#!/bin/sh\necho \"$@\" >> " + log.toUtf8() + "\ncase \"$*\" in *dead00000000000000000000000000ff*) exit 1;; esac\nexit 0\n", true);
        ContainerExec exec(u"sh "_s + tool);
        ChannelImporter imp(&exec);
        QSignalSpy sp(&imp, &ChannelImporter::finished);
        QString err;
        QVERIFY(!imp.start(u"abc; rm -rf /"_s, &err));
        QVERIFY(imp.start(u"c9d7f950ab6b5a1199e3d6c10d7f0103"_s, &err));
        QVERIFY(sp.wait(8000));
        QVERIFY(sp.first().at(1).toBool());
        QFile f(log); QVERIFY(f.open(QIODevice::ReadOnly));
        const QStringList lines = QString::fromUtf8(f.readAll()).split(u'\n', Qt::SkipEmptyParts);
        QCOMPARE(lines, QStringList({u"kolibri manage importchannel network c9d7f950ab6b5a1199e3d6c10d7f0103"_s,
                                     u"kolibri manage importcontent network c9d7f950ab6b5a1199e3d6c10d7f0103"_s}));
        sp.clear();
        QVERIFY(imp.start(u"dead00000000000000000000000000ff"_s, &err));
        QVERIFY(sp.wait(8000));
        QVERIFY(!sp.first().at(1).toBool());   // the channel step failed: the content step never ran
        QFile g(log); QVERIFY(g.open(QIODevice::ReadOnly));
        QVERIFY(!QString::fromUtf8(g.readAll()).contains(u"importcontent network dead"_s));
    }
};

QTEST_MAIN(TstSurvivalPacks)
#include "tst_survival_packs.moc"
