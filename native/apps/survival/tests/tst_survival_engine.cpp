// SPDX-License-Identifier: AGPL-3.0-or-later
// The Survival Studio engines, driven the way the window and the CLI drive them: the ZIM reader, the article
// sanitiser (the security boundary of the reader), the pack manager (verify refuses a bad checksum, delete removes
// files), the radio detector (fake sysfs and a fake serial port), PMTiles and vector tiles, kits and saved state.
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "articlehtml.h"
#include "fixtures.h"
#include "kit.h"
#include "mapdata.h"
#include "packmanager.h"
#include "pmtiles.h"
#include "radiodetector.h"
#include "statestore.h"
#include "zimarchive.h"

using namespace Taos::Survival;
using namespace Qt::StringLiterals;

namespace {
void writeFile(const QString &p, const QByteArray &d)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    QVERIFY2(f.open(QIODevice::WriteOnly), qPrintable(p));
    f.write(d);
}
// every tag that is left in the output
QStringList tagsIn(const QString &html)
{
    QStringList out;
    QRegularExpressionMatchIterator it = QRegularExpression(QStringLiteral("</?([A-Za-z0-9]+)")).globalMatch(html);
    while (it.hasNext())
        out << it.next().captured(1).toLower();
    out.removeDuplicates();
    return out;
}
class FakeSerial : public SerialIo
{
public:
    FakeSerial(QByteArray answer, bool opens = true) : m_answer(std::move(answer)), m_opens(opens) {}
    bool open(QString *e) override { if (!m_opens && e) *e = QStringLiteral("Cannot open the port"); return m_opens; }
    bool write(const QByteArray &d) override { written += d; return true; }
    QByteArray read(int) override { QByteArray a = m_answer; m_answer.clear(); return a; }
    void close() override { closed = true; }
    QByteArray written;
    bool closed = false;
private:
    QByteArray m_answer;
    bool m_opens;
};
// a framed FromRadio holding field `field` with a payload
QByteArray frame(int field, const QByteArray &payload)
{
    QByteArray body;
    Fixture::putBytes(body, field, payload);
    QByteArray f;
    f.append(char(0x94)).append(char(0xC3)).append(char((body.size() >> 8) & 0xff)).append(char(body.size() & 0xff));
    return f + body;
}
} // namespace

class TstSurvivalEngine : public QObject
{
    Q_OBJECT
    QTemporaryDir m_dir;

    QString path(const QString &rel) const { return m_dir.filePath(rel); }

private Q_SLOTS:
    void initTestCase()
    {
        QVERIFY(m_dir.isValid());
        qputenv("TAOS_SURVIVAL_ALLOW_FILE_URLS", "1");
    }

    // ---- ZIM ----------------------------------------------------------------------------------------------------
    void zim_open_info_search_entry()
    {
        const QString f = path(u"lib.zim"_s);
        QVERIFY(Fixture::writeZim(f));
        QString err;
        auto z = ZimArchive::open(f, &err);
        QVERIFY2(z, qPrintable(err));
        QCOMPARE(z->info().title, u"Test library"_s);
        QCOMPARE(z->info().language, u"eng"_s);
        QVERIFY(z->info().hasFulltext);
        QCOMPARE(z->info().mainPath, u"index"_s);
        // titles
        const auto byTitle = z->suggest(u"Water"_s);
        QVERIFY(!byTitle.isEmpty());
        QCOMPARE(byTitle.first().path, u"water"_s);
        // full text finds a word that is only in the body
        const auto byText = z->fullText(u"bacteria"_s);
        QVERIFY(!byText.isEmpty());
        QCOMPARE(byText.first().path, u"water"_s);
        // search() gives title hits first and does not repeat a page
        const auto both = z->search(u"water"_s);
        int n = 0;
        for (const auto &h : both) n += h.path == u"water"_s;
        QCOMPARE(n, 1);
        QVERIFY(z->search(u"zzzznotthere"_s).isEmpty());
        // entries
        const ZimEntry e = z->entry(u"water"_s);
        QVERIFY(e.found);
        QCOMPARE(e.mimeType, u"text/html"_s);
        QVERIFY(e.data.contains("Boil the water"));
        QVERIFY(!z->entry(u"nope"_s).found);
        QVERIFY(z->entry(u"I/drop.png"_s).mimeType.startsWith(u"image/png"_s));
        QVERIFY(!z->randomPath().isEmpty());
    }
    void zim_garbage_file_is_refused_with_a_reason()
    {
        writeFile(path(u"bad.zim"_s), "this is not a zim file at all");
        QString err;
        QVERIFY(!ZimArchive::open(path(u"bad.zim"_s), &err));
        QVERIFY(!err.isEmpty());
        QVERIFY(!ZimArchive::open(path(u"missing.zim"_s), &err));
    }

    // ---- sanitiser ----------------------------------------------------------------------------------------------
    void sanitiser_strips_scripts_and_styles_with_their_content()
    {
        const auto r = sanitizeArticle("<p>a</p><script>alert('x')</script><SCRIPT SRC=//e/x.js></SCRIPT><style>p{background:url(http://e/x)}</style>"
                                       "<noscript><img src=x></noscript><p>b</p>", u"p"_s, u"A/x"_s);
        QVERIFY(!r.html.contains(u"alert"_s));
        QVERIFY(!r.html.contains(u"script"_s, Qt::CaseInsensitive));
        QVERIFY(!r.html.contains(u"background"_s));
        QVERIFY(!r.html.contains(u"http"_s));
        QVERIFY(r.html.contains(u"<p>a</p>"_s) && r.html.contains(u"<p>b</p>"_s));
    }
    void sanitiser_drops_every_active_or_remote_element()
    {
        const QByteArray evil =
            "<iframe src=\"http://e/\"></iframe><object data=\"x\"></object><embed src=x><applet code=x></applet><svg onload=alert(1)><circle/></svg>"
            "<math><mi>x</mi></math><form action=\"http://e/\"><input name=a><button>go</button></form><video src=x></video><audio src=x></audio>"
            "<link rel=stylesheet href=\"http://e/x.css\"><meta http-equiv=refresh content=\"0;url=http://e/\"><base href=\"http://e/\">"
            "<canvas></canvas><template><p>t</p></template><dialog>d</dialog><p>kept</p>";
        const auto r = sanitizeArticle(evil, u"p"_s, u"A/x"_s);
        for (const QString &t : tagsIn(r.html))
            QVERIFY2(QStringList({u"p"_s}).contains(t), qPrintable(t));
        QVERIFY(r.html.contains(u"kept"_s));
        QVERIFY(!r.html.contains(u"http"_s));
    }
    void sanitiser_removes_event_handlers_styles_and_unknown_attributes()
    {
        const auto r = sanitizeArticle("<p onclick=\"alert(1)\" style=\"background:url(http://e/x)\" class=a id=b>x</p>"
                                       "<img src=\"I/a.png\" onerror=\"alert(1)\" srcset=\"http://e/a.png 2x\" style=\"x:url(//e)\">"
                                       "<b onmouseover=x>y</b>", u"p"_s, u"A/x"_s);
        QVERIFY(!r.html.contains(u"onclick"_s));
        QVERIFY(!r.html.contains(u"onerror"_s));
        QVERIFY(!r.html.contains(u"onmouseover"_s));
        QVERIFY(!r.html.contains(u"style"_s));
        QVERIFY(!r.html.contains(u"srcset"_s));
        QVERIFY(!r.html.contains(u"http"_s));
        QVERIFY(!r.html.contains(u"class"_s));
        QVERIFY(r.html.contains(u"<p>x</p>"_s));
    }
    void sanitiser_links_only_into_the_pack()
    {
        const struct { const char *href; bool kept; } cases[] = {
            {"javascript:alert(1)", false}, {"JaVaScRiPt:alert(1)", false}, {"java\tscript:alert(1)", false}, {"java\nscript:alert(1)", false},
            {"&#106;avascript:alert(1)", false}, {"&#x6A;avascript:alert(1)", false}, {" javascript:alert(1)", false},
            {"data:text/html,<script>1</script>", false}, {"vbscript:x", false}, {"http://evil.example/", false}, {"https://evil.example/", false},
            {"//evil.example/x", false}, {"ftp://e/x", false}, {"file:///etc/passwd", false}, {"#frag", false}, {"..\\..\\x", false},
            {"../../../etc/passwd", true},   /* in the pack: it can only ever name a pack entry, never a file */
            {"Other_article", true}, {"../A/Other", true}, {"/A/Other", true}, {"Other%20article#top", true}};
        for (const auto &c : cases) {
            const auto r = sanitizeArticle(QByteArray("<a href=\"") + c.href + "\">link</a>", u"pk"_s, u"A/Here"_s);
            QVERIFY2(r.html.contains(u"href=\"zim:pk/"_s) == c.kept, c.href);
            QVERIFY2(r.html.contains(u"link"_s), c.href);   // the words always stay
            if (!c.kept)
                QVERIFY2(!r.html.contains(u"href"_s), c.href);
        }
        // a link resolves against the article it is in
        QCOMPARE(sanitizeArticle("<a href=\"../I/x\">l</a>", u"pk"_s, u"A/B/Here"_s).links, QStringList({u"A/I/x"_s}));
        QCOMPARE(resolvePackPath(u"A/Here"_s, u"x/../y#z"_s), u"A/y"_s);
        QCOMPARE(resolvePackPath(u"Here"_s, u"../up"_s), u"up"_s);   // ".." above the root stays at the root
        QCOMPARE(resolvePackPath(u"A/Here"_s, u"..\\up"_s), QString());
    }
    void sanitiser_images_come_from_the_pack_only()
    {
        const auto r = sanitizeArticle(
            "<img src=\"../I/drop.png\" width=\"20\" height=\"10\">"
            "<img src=\"http://evil/x.png\"><img src=\"//evil/x.png\"><img src=\"https://evil/x\"><img src=\"data:image/png;base64,AAAA\">"
            "<img src=\"javascript:1\"><img srcset=\"http://e/a 1x\"><img>", u"pk"_s, u"A/Here"_s);
        QCOMPARE(r.images, QStringList({u"I/drop.png"_s}));
        QVERIFY(r.html.contains(u"src=\"image://zimimg/pk/I/drop.png\""_s));
        QVERIFY(r.html.contains(u"width=\"20\""_s));
        QVERIFY(!r.html.contains(u"evil"_s));
        QVERIFY(!r.html.contains(u"data:"_s));
        QVERIFY(r.blockedRemote >= 5);
        // every address in the output is either zim: or image://zimimg/
        QRegularExpressionMatchIterator it = QRegularExpression(QStringLiteral("(?:src|href)=\"([^\"]*)\"")).globalMatch(r.html);
        while (it.hasNext()) {
            const QString v = it.next().captured(1);
            QVERIFY2(v.startsWith(u"image://zimimg/"_s) || v.startsWith(u"zim:"_s), qPrintable(v));
        }
    }
    void sanitiser_survives_tag_soup_and_rebuilds_cleanly()
    {
        const QByteArray soup[] = {"<scr<script>ipt>alert(1)</scr</script>ipt>", "<<script>script>alert(1)<</script>/script>", "<!--><script>alert(1)</script>-->",
            "<p <script>alert(1)</script>>x", "<a href=\"x\" onclick=alert(1) <b>", "<p>unclosed <b>bold <i>italic", "<table><tr><td>cell", "<img src=\"x\" <script>",
            "</p></b></div><p>stray closers", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>", "<p>&#60;script&#62;alert(1)&#60;/script&#62;</p>"};
        for (const QByteArray &s : soup) {
            const auto r = sanitizeArticle(s, u"pk"_s, u"A/x"_s);
            QVERIFY2(!r.html.contains(u"<script"_s, Qt::CaseInsensitive), s.constData());
            QVERIFY2(!r.html.contains(u"alert(1)</"_s) || r.html.contains(u"&lt;"_s), s.constData());
            // what we wrote is clean on a second pass too, and uses no tag we did not write the first time
            const auto again = sanitizeArticle(r.html.toUtf8(), u"pk"_s, u"A/x"_s);
            for (const QString &t : tagsIn(again.html))
                QVERIFY2(tagsIn(r.html).contains(t), s.constData());
            QVERIFY2(!again.html.contains(u"<script"_s, Qt::CaseInsensitive), s.constData());
        }
        // text that looked like markup stays text
        const auto t = sanitizeArticle("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>", u"pk"_s, u"A/x"_s);
        QVERIFY(t.html.contains(u"&lt;script&gt;"_s));
    }
    void sanitiser_drops_hidden_and_junk_blocks_and_reads_the_title()
    {
        const auto r = sanitizeArticle("<html><head><title>My title</title></head><body><div class=\"navbox\">NAV</div><div style=\"display:none\">HIDDEN</div>"
                                       "<span hidden>H2</span><h1>Head</h1><p>shown</p></body></html>", u"pk"_s, u"A/x"_s);
        QCOMPARE(r.title, u"My title"_s);
        QVERIFY(!r.html.contains(u"NAV"_s) && !r.html.contains(u"HIDDEN"_s) && !r.html.contains(u"H2"_s));
        QVERIFY(r.html.contains(u"shown"_s));
        QCOMPARE(sanitizeArticle("<h1>Only heading</h1>", u"pk"_s, u"A/x"_s).title, u"Only heading"_s);
    }
    void sanitiser_real_article_from_a_zim()
    {
        const QString f = path(u"lib2.zim"_s);
        QVERIFY(Fixture::writeZim(f));
        auto z = ZimArchive::open(f);
        QVERIFY(z);
        const auto r = sanitizeArticle(z->entry(u"water"_s).data, u"lib"_s, u"water"_s);
        QVERIFY(!r.html.contains(u"alert"_s));
        QVERIFY(!r.html.contains(u"evil.example"_s));
        QCOMPARE(r.images, QStringList({u"I/drop.png"_s}));
        QCOMPARE(r.blockedRemote, 1);
        QCOMPARE(r.title, u"Water purification"_s);
    }

    // ---- packs --------------------------------------------------------------------------------------------------
private:
    QByteArray catalogue(const QString &id, const QString &file, const QString &url, const QByteArray &sha, bool needsAccept = false)
    {
        return QJsonDocument(QJsonObject{{u"version"_s, 1}, {u"packs"_s, QJsonArray{QJsonObject{
            {u"id"_s, id}, {u"title"_s, id}, {u"kind"_s, u"zim"_s}, {u"file"_s, file}, {u"url"_s, url}, {u"bytes"_s, 12},
            {u"sha256"_s, QString::fromLatin1(sha)}, {u"licence"_s, u"CC0-1.0"_s}, {u"version"_s, u"1"_s}, {u"needsAcceptance"_s, needsAccept}}}}}).toJson();
    }
private Q_SLOTS:
    void packs_download_verifies_and_installs()
    {
        const QString src = path(u"srv/good.zim"_s);
        writeFile(src, "0123456789ab");
        QTemporaryDir d;
        PackManager pm(d.path());
        QString err;
        QVERIFY(pm.loadCatalogue(catalogue(u"good"_s, u"good.zim"_s, QUrl::fromLocalFile(src).toString(),
                                           PackManager::sha256Of(src).toLatin1()), &err));
        QCOMPARE(pm.pack(u"good"_s).state, u"available"_s);
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(u"good"_s, &err));
        QVERIFY(done.wait(8000));
        QVERIFY(done.first().at(1).toBool());
        const PackInfo p = pm.pack(u"good"_s);
        QVERIFY(p.installed);
        QCOMPARE(p.state, u"installed"_s);
        QVERIFY(QFile::exists(pm.packsDir() + u"/good.zim"_s));
        QVERIFY(!QFile::exists(pm.packsDir() + u"/good.zim.part"_s));
        QCOMPARE(pm.verifyInstalled(u"good"_s), VerifyResult::Ok);
        // an installed pack is not downloaded again over itself
        QVERIFY(!pm.download(u"good"_s, &err));
        QVERIFY(err.contains(u"already installed"_s));
    }
    void packs_a_bad_checksum_is_refused_and_nothing_is_kept()
    {
        const QString src = path(u"srv/tampered.zim"_s);
        writeFile(src, "0123456789ab");
        QTemporaryDir d;
        PackManager pm(d.path());
        const QByteArray wrong(64, 'a');
        QVERIFY(pm.loadCatalogue(catalogue(u"bad"_s, u"tampered.zim"_s, QUrl::fromLocalFile(src).toString(), wrong)));
        QSignalSpy done(&pm, &PackManager::finished);
        QVERIFY(pm.download(u"bad"_s));
        QVERIFY(done.wait(8000));
        QVERIFY(!done.first().at(1).toBool());
        QVERIFY(done.first().at(2).toString().contains(u"checksum"_s));
        const PackInfo p = pm.pack(u"bad"_s);
        QVERIFY(!p.installed);
        QCOMPARE(p.state, u"failed"_s);
        QVERIFY(!QFile::exists(pm.packsDir() + u"/tampered.zim"_s));
        QVERIFY(!QFile::exists(pm.packsDir() + u"/tampered.zim.part"_s));
        QVERIFY(!p.error.isEmpty());
    }
    void packs_verify_of_an_installed_file_catches_a_changed_file()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const QString f = pm.packsDir() + u"/a.zim"_s;
        writeFile(f, "original");
        const QString sha = PackManager::sha256Of(f);
        QCOMPARE(PackManager::verifyFile(f, sha), VerifyResult::Ok);
        QCOMPARE(PackManager::verifyFile(f, QString(64, QLatin1Char('0'))), VerifyResult::Mismatch);
        QCOMPARE(PackManager::verifyFile(f, QString()), VerifyResult::NoChecksum);
        QCOMPARE(PackManager::verifyFile(path(u"nofile"_s), sha), VerifyResult::Unreadable);
        QVERIFY(pm.loadCatalogue(catalogue(u"a"_s, u"a.zim"_s, u"https://example.org/a.zim"_s, sha.toLatin1())));
        QCOMPARE(pm.verifyInstalled(u"a"_s), VerifyResult::Ok);
        writeFile(f, "changed!");
        QCOMPARE(pm.verifyInstalled(u"a"_s), VerifyResult::Mismatch);
    }
    void packs_delete_removes_the_files()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        QVERIFY(pm.loadCatalogue(catalogue(u"a"_s, u"a.zim"_s, u"https://example.org/a.zim"_s, QByteArray(64, 'b'))));
        const QString dir = pm.packsDir();
        writeFile(dir + u"/a.zim"_s, "x");
        writeFile(dir + u"/a.zim.part"_s, "y");
        writeFile(dir + u"/a.zim.taospack.json"_s, "{}");
        QVERIFY(pm.pack(u"a"_s).installed);
        QString err;
        QVERIFY2(pm.remove(u"a"_s, &err), qPrintable(err));
        QVERIFY(!QFile::exists(dir + u"/a.zim"_s));
        QVERIFY(!QFile::exists(dir + u"/a.zim.part"_s));
        QVERIFY(!QFile::exists(dir + u"/a.zim.taospack.json"_s));
        QVERIFY(!pm.pack(u"a"_s).installed);
        // a file the catalogue does not know is a local pack, and goes the same way
        writeFile(dir + u"/mine.pmtiles"_s, "m");
        pm.rescan();
        QVERIFY(pm.pack(u"local-mine"_s).installed);
        QVERIFY(pm.remove(u"local-mine"_s));
        QVERIFY(!QFile::exists(dir + u"/mine.pmtiles"_s));
        QVERIFY(!pm.remove(u"does-not-exist"_s, &err));
    }
    void packs_import_checks_the_checksum_first()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        const QString src = path(u"srv/imp.zim"_s);
        writeFile(src, "payload");
        QString err;
        QVERIFY(!pm.importFile(src, QString(64, QLatin1Char('1')), &err));
        QVERIFY(!QFile::exists(pm.packsDir() + u"/imp.zim"_s));
        QVERIFY(pm.importFile(src, PackManager::sha256Of(src), &err));
        QVERIFY(QFile::exists(pm.packsDir() + u"/imp.zim"_s));
        QVERIFY(!pm.importFile(src, QString(), &err));   // already there
        writeFile(path(u"srv/readme.txt"_s), "x");
        QVERIFY(!pm.importFile(path(u"srv/readme.txt"_s), QString(), &err));
    }
    void packs_refuse_to_start_without_checksum_licence_or_address()
    {
        QTemporaryDir d;
        PackManager pm(d.path());
        QString err;
        QVERIFY(pm.loadCatalogue(catalogue(u"nosum"_s, u"n.zim"_s, u"https://example.org/n.zim"_s, QByteArray())));
        QVERIFY(!pm.download(u"nosum"_s, &err));
        QVERIFY(err.contains(u"checksum"_s));
        QVERIFY(pm.loadCatalogue(catalogue(u"lic"_s, u"l.zim"_s, u"https://example.org/l.zim"_s, QByteArray(64, 'c'), true)));
        QVERIFY(!pm.download(u"lic"_s, &err));
        QVERIFY(err.contains(u"licence"_s));
        pm.acceptLicence(u"lic"_s);
        QVERIFY(pm.licenceAccepted(u"lic"_s));
        QVERIFY(pm.loadCatalogue(catalogue(u"noaddr"_s, u"x.zim"_s, QString(), QByteArray(64, 'd'))));
        QVERIFY(!pm.download(u"noaddr"_s, &err));
        QVERIFY(!pm.download(u"unknown"_s, &err));
        // a file: address works only for the tests
        qunsetenv("TAOS_SURVIVAL_ALLOW_FILE_URLS");
        QVERIFY(pm.loadCatalogue(catalogue(u"f"_s, u"f.zim"_s, u"file:///etc/passwd"_s, QByteArray(64, 'e'))));
        QVERIFY(!pm.download(u"f"_s, &err));
        qputenv("TAOS_SURVIVAL_ALLOW_FILE_URLS", "1");
        // a malformed catalogue entry (a path in the file name) is skipped, not trusted
        QVERIFY(pm.loadCatalogue(catalogue(u"evil"_s, u"../evil.zim"_s, u"https://example.org/e.zim"_s, QByteArray(64, 'f'))));
        QVERIFY(!pm.contains(u"evil"_s));
        QVERIFY(!pm.loadCatalogue("{ not json", &err));
    }

    // ---- radios -------------------------------------------------------------------------------------------------
private:
    void makeUsbTty(const QString &root, const QString &tty, const QString &vid, const QString &pid, const QString &product)
    {
        const QString usb = root + u"/sys/devices/pci0000:00/usb1/1-"_s + tty;
        const QString iface = usb + u"/1-"_s + tty + u":1.0"_s;
        const QString ttyDir = iface + u"/tty/"_s + tty;
        QDir().mkpath(ttyDir);
        writeFile(usb + u"/idVendor"_s, vid.toUtf8() + "\n");
        writeFile(usb + u"/idProduct"_s, pid.toUtf8() + "\n");
        writeFile(usb + u"/manufacturer"_s, "Test Maker\n");
        writeFile(usb + u"/product"_s, product.toUtf8() + "\n");
        writeFile(usb + u"/serial"_s, "SER" + tty.toUtf8() + "\n");
        QDir().mkpath(root + u"/sys/class/tty"_s);
        QFile::link(ttyDir, root + u"/sys/class/tty/"_s + tty);
        QFile::link(iface, ttyDir + u"/device"_s);
        QDir().mkpath(root + u"/dev"_s);
        writeFile(root + u"/dev/"_s + tty, QByteArray());
    }
private Q_SLOTS:
    void radios_none_connected_is_an_empty_list()
    {
        QTemporaryDir r;
        QDir().mkpath(r.path() + u"/sys/class/tty"_s);
        RadioDetector det(r.path() + u"/sys"_s, r.path() + u"/dev"_s, r.path() + u"/proc"_s);
        QVERIFY(det.scan().isEmpty());
        // a missing sysfs is not an error either
        RadioDetector none(r.path() + u"/nosys"_s, r.path() + u"/dev"_s, r.path() + u"/proc"_s);
        QVERIFY(none.scan().isEmpty());
    }
    void radios_usb_serial_devices_are_listed_with_what_the_id_says()
    {
        QTemporaryDir r;
        makeUsbTty(r.path(), u"ttyACM0"_s, u"303a"_s, u"1001"_s, u"USB JTAG/serial debug unit"_s);
        makeUsbTty(r.path(), u"ttyUSB0"_s, u"10c4"_s, u"ea60"_s, u"CP2102 USB to UART Bridge"_s);
        makeUsbTty(r.path(), u"ttyUSB1"_s, u"1234"_s, u"5678"_s, u"Some modem"_s);
        // a serial port that is not on USB is not a radio
        QDir().mkpath(r.path() + u"/sys/devices/platform/serial8250/tty/ttyS0"_s);
        QFile::link(r.path() + u"/sys/devices/platform/serial8250/tty/ttyS0"_s, r.path() + u"/sys/class/tty/ttyS0"_s);
        RadioDetector det(r.path() + u"/sys"_s, r.path() + u"/dev"_s, r.path() + u"/proc"_s);
        const auto list = det.scan();
        QCOMPARE(list.size(), 3);
        QCOMPARE(list[0].device, r.path() + u"/dev/ttyACM0"_s);
        QCOMPARE(list[0].vid, quint16(0x303a));
        QCOMPARE(list[0].pid, quint16(0x1001));
        QCOMPARE(list[0].product, u"USB JTAG/serial debug unit"_s);
        QCOMPARE(list[0].confidence, u"possible"_s);
        QVERIFY(list[0].chip.contains(u"Espressif"_s));
        QCOMPARE(list[1].confidence, u"possible"_s);   // CP210x
        QCOMPARE(list[2].confidence, u"unlikely"_s);   // unknown id
        // a scan never claims a protocol: only a handshake can
        for (const auto &c : list) {
            QVERIFY(c.protocol.isEmpty());
            QVERIFY(!c.probed);
        }
    }
    void radios_meshtastic_request_is_framed_as_documented()
    {
        const QByteArray req = RadioDetector::meshtasticRequest(1);
        QVERIFY(req.startsWith(QByteArray(32, char(0xC3))));
        const QByteArray framed = req.mid(32);
        QCOMPARE(quint8(framed[0]), quint8(0x94));
        QCOMPARE(quint8(framed[1]), quint8(0xC3));
        const int len = (quint8(framed[2]) << 8) | quint8(framed[3]);
        QCOMPARE(framed.size(), 4 + len);
        QCOMPARE(quint8(framed[4]), quint8(0x18));   // ToRadio.want_config_id (field 3, varint)
        QCOMPARE(quint8(framed[5]), quint8(1));
    }
    void radios_handshake_confirms_meshtastic_and_reads_the_firmware()
    {
        QByteArray meta;
        Fixture::putBytes(meta, 1, "2.5.9.abc");   // DeviceMetadata.firmware_version
        QByteArray stream = QByteArray("some boot log\r\n") + frame(3, QByteArray("\x08\x01", 2)) + frame(13, meta);
        FakeSerial io(stream);
        const ProbeResult r = RadioDetector::probeWith(io);
        QVERIFY(r.identified);
        QCOMPARE(r.protocol, u"meshtastic"_s);
        QCOMPARE(r.firmware, u"2.5.9.abc"_s);
        QVERIFY(io.closed);
        QVERIFY(io.written.startsWith(QByteArray(32, char(0xC3))));   // it asked, it did not transmit anything else
    }
    void radios_handshake_rejects_silence_noise_and_closed_ports()
    {
        {
            FakeSerial io{QByteArray()};
            const ProbeResult r = RadioDetector::probeWith(io);
            QVERIFY(!r.identified);
            QVERIFY(r.note.contains(u"No answer"_s));
            QVERIFY(io.closed);
        }
        {
            FakeSerial io(QByteArray("just text, no frames\r\n"));
            QVERIFY(!RadioDetector::probeWith(io).identified);
        }
        {
            // a frame that claims more than 512 bytes is corrupt
            QByteArray bad;
            bad.append(char(0x94)).append(char(0xC3)).append(char(0x7f)).append(char(0xff)).append(QByteArray(20, 'x'));
            FakeSerial io(bad);
            QVERIFY(!RadioDetector::probeWith(io).identified);
        }
        {
            // a frame whose payload is not protobuf
            QByteArray junk;
            junk.append(char(0x94)).append(char(0xC3)).append(char(0)).append(char(4)).append(QByteArray("\xff\xff\xff\xff", 4));
            FakeSerial io(junk);
            QVERIFY(!RadioDetector::probeWith(io).identified);
        }
        {
            FakeSerial io(QByteArray(), false);
            const ProbeResult r = RadioDetector::probeWith(io);
            QVERIFY(!r.identified);
            QVERIFY(r.note.contains(u"Cannot open"_s));
        }
    }

    // ---- maps ---------------------------------------------------------------------------------------------------
    void pmtiles_tile_ids_follow_the_spec()
    {
        QCOMPARE(PmTilesArchive::tileId(0, 0, 0), quint64(0));
        QCOMPARE(PmTilesArchive::tileId(1, 0, 0), quint64(1));
        QCOMPARE(PmTilesArchive::tileId(1, 0, 1), quint64(2));
        QCOMPARE(PmTilesArchive::tileId(1, 1, 1), quint64(3));
        QCOMPARE(PmTilesArchive::tileId(1, 1, 0), quint64(4));
        QCOMPARE(PmTilesArchive::tileId(2, 0, 0), quint64(5));
    }
    void pmtiles_open_read_and_decode()
    {
        for (const bool gz : {true, false}) {
            const QString f = path(gz ? u"m-gz.pmtiles"_s : u"m.pmtiles"_s);
            QVERIFY(Fixture::writePmtiles(f, gz));
            QString err;
            auto a = PmTilesArchive::open(f, &err);
            QVERIFY2(a, qPrintable(err));
            QVERIFY(a->isVector());
            QCOMPARE(a->name(), u"Test map"_s);
            QCOMPARE(a->attribution(), QString::fromUtf8("© Test contributors"));   // markup removed
            const QByteArray t = a->tile(0, 0, 0);
            QVERIFY(!t.isEmpty());
            QVERIFY(a->tile(1, 0, 0).isEmpty());
            QVERIFY(a->tile(5, 0, 0).isEmpty());   // outside the zoom range
            const MvtTile mvt = decodeMvt(t);
            QVERIFY(mvt.ok);
            QCOMPARE(mvt.layers.size(), 4);
            const MvtLayer *places = mvt.layer(u"places"_s);
            QVERIFY(places);
            QCOMPARE(places->features.size(), 1);
            QCOMPARE(places->features[0].type, 1);
            QCOMPARE(places->features[0].parts[0][0], QPointF(2048, 2048));
            QCOMPARE(places->features[0].str("name:en"), u"Testville"_s);
            const MvtLayer *earth = mvt.layer(u"earth"_s);
            QVERIFY(earth && earth->features[0].type == 3);
            QCOMPARE(earth->features[0].parts[0].size(), 5);   // closed ring
            QCOMPARE(mvt.layer(u"roads"_s)->features[0].parts[0].last(), QPointF(4096, 3000));
        }
    }
    void pmtiles_refuses_files_that_are_not_pmtiles()
    {
        writeFile(path(u"x.pmtiles"_s), QByteArray(300, 'z'));
        QString err;
        QVERIFY(!PmTilesArchive::open(path(u"x.pmtiles"_s), &err));
        QVERIFY(err.contains(u"PMTiles"_s));
        QVERIFY(!PmTilesArchive::open(path(u"nothere.pmtiles"_s), &err));
        // a truncated tile is an empty result, never a crash
        QVERIFY(decodeMvt(Fixture::sampleMvt().left(40)).layers.size() <= 4);
        QVERIFY(decodeMvt(QByteArray("\xff\xff\xff")).layers.isEmpty());
    }
    void map_style_and_place_search()
    {
        const QString f = path(u"m2.pmtiles"_s);
        QVERIFY(Fixture::writePmtiles(f));
        QString err;
        auto m = MapData::open(f, &err);
        QVERIFY2(m, qPrintable(err));
        const auto tile = m->tile(0, 0, 0);
        QVERIFY(tile && tile->hasData);
        QVERIFY(!tile->fills[FillEarth].isEmpty());
        QVERIFY(!tile->fills[FillWater].isEmpty());
        QVERIFY(!tile->lines[LineMajor].isEmpty());
        QCOMPARE(tile->labels.size(), 1);
        QCOMPARE(tile->labels[0].cls, int(LabelCity));
        QVERIFY(!m->tile(3, 0, 0));   // no such tile
        QVERIFY(!m->placeIndexReady());
        m->buildPlaceIndex(9);
        QVERIFY(m->placeIndexReady());
        QCOMPARE(m->placeCount(), 1);
        const auto hits = m->searchPlaces(u"testv"_s);
        QCOMPARE(hits.size(), 1);
        QCOMPARE(hits[0].name, QLocale::system().bcp47Name().startsWith(u"en"_s) ? u"Testville"_s : u"Teststadt"_s);
        QVERIFY(qAbs(hits[0].lon) < 0.01 && qAbs(hits[0].lat) < 0.01);   // the middle of the world tile
        QCOMPARE(m->searchPlaces(u"Teststadt"_s).size(), 1);   // the local name finds it too
        QVERIFY(m->searchPlaces(u"nowhere"_s).isEmpty());
        // Web Mercator round trip
        QVERIFY(qAbs(worldXToLon(lonToWorldX(-4.25, 7), 7) + 4.25) < 1e-9);
        QVERIFY(qAbs(worldYToLat(latToWorldY(55.86, 7), 7) - 55.86) < 1e-9);
    }
    void map_pack_that_is_not_vector_is_refused_in_words()
    {
        QString err;
        writeFile(path(u"nope.pmtiles"_s), QByteArray(300, 'q'));
        QVERIFY(!MapData::open(path(u"nope.pmtiles"_s), &err));
        QVERIFY(!err.isEmpty());
    }

    // ---- kits ---------------------------------------------------------------------------------------------------
    void kits_say_unknown_when_they_cannot_tell()
    {
        const QJsonObject manifest = QJsonDocument::fromJson(R"({"kits":[{"id":"k","title":"K","hardware":"h","summary":"s","items":[
            {"id":"a","title":"A","check":"zim","min":1},{"id":"b","title":"B","check":"battery"},{"id":"c","title":"C","check":"storage","min":1},
            {"id":"d","title":"D","check":"radio","min":1},{"id":"e","title":"E","check":"manual","note":"by hand"},{"id":"f","title":"F","check":"position"}]}]})").object();
        KitContext ctx;   // nothing known
        auto status = [&](const KitContext &c, int i) { return evaluateKits(manifest, c)[0].toObject()[u"items"_s].toArray()[i].toObject()[u"status"_s].toString(); };
        QCOMPARE(status(ctx, 0), u"missing"_s);
        QCOMPARE(status(ctx, 1), u"unknown"_s);   // battery -1: not measured, not "none"
        QCOMPARE(status(ctx, 2), u"unknown"_s);
        QCOMPARE(status(ctx, 3), u"missing"_s);
        QCOMPARE(status(ctx, 4), u"unknown"_s);
        QCOMPARE(status(ctx, 5), u"unknown"_s);
        ctx.zimPacks = 2; ctx.batteries = 1; ctx.freeBytes = 5LL << 30; ctx.radiosPossible = 1; ctx.positionSources = 1;
        QCOMPARE(status(ctx, 0), u"ok"_s);
        QCOMPARE(status(ctx, 1), u"ok"_s);
        QCOMPARE(status(ctx, 2), u"ok"_s);
        QCOMPARE(status(ctx, 3), u"unknown"_s);   // a possible radio is not a confirmed one
        ctx.radiosConfirmed = 1;
        QCOMPARE(status(ctx, 3), u"ok"_s);
        ctx.batteries = 0;
        QCOMPARE(status(ctx, 1), u"missing"_s);
        const QJsonObject k = evaluateKits(manifest, ctx)[0].toObject();
        QCOMPARE(k[u"total"_s].toInt(), 6);
        QCOMPARE(k[u"ready"_s].toInt(), 4);
    }
    void kits_bundled_manifest_is_valid()
    {
        QFile f(QStringLiteral(SURVIVAL_CATALOG_DIR "/kits.json"));
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
        const auto kits = evaluateKits(o, KitContext());
        QVERIFY(kits.size() >= 4);
        for (const QJsonValue &k : kits)
            QVERIFY(k.toObject()[u"total"_s].toInt() > 0);
        QFile p(QStringLiteral(SURVIVAL_CATALOG_DIR "/packs.json"));
        QVERIFY(p.open(QIODevice::ReadOnly));
        PackManager pm(path(u"cat"_s));
        QVERIFY(pm.loadCatalogue(p.readAll()));
        for (const PackInfo &i : pm.packs()) {
            QVERIFY(!i.licence.isEmpty());
            QVERIFY(!i.attribution.isEmpty());
            QVERIFY2(!i.url.isEmpty() ? i.url.scheme() == u"https"_s && i.sha256.size() == 64 : !i.recipe.isEmpty(), qPrintable(i.id));
        }
    }

    // ---- saved state --------------------------------------------------------------------------------------------
    void state_markers_round_trip_and_a_damaged_file_is_never_overwritten()
    {
        QTemporaryDir d;
        StateStore s(d.path());
        QVERIFY(s.load());
        const QString id = s.addMarker(u"Spring"_s, u"clean water"_s, -4.25, 55.86);
        s.addContact(u"ALPHA"_s, u"!1234abcd"_s, QString());
        s.setMe(-4.0, 55.0);
        QString err;
        QVERIFY2(s.save(&err), qPrintable(err));
        StateStore again(d.path());
        QVERIFY(again.load());
        QCOMPARE(again.markers().size(), 1);
        QCOMPARE(again.markers()[0].toObject()[u"name"_s].toString(), u"Spring"_s);
        QCOMPARE(again.contacts().size(), 1);
        QVERIFY(again.hasMe());
        QVERIFY(again.removeMarker(id));
        QVERIFY(!again.removeMarker(id));
        // damaged file: load fails and the file is left as it was
        writeFile(d.path() + u"/state.json"_s, "{ broken");
        StateStore broken(d.path());
        QVERIFY(!broken.load());
        QFile f(d.path() + u"/state.json"_s);
        QVERIFY(f.open(QIODevice::ReadOnly));
        QCOMPARE(f.readAll(), QByteArray("{ broken"));
    }

    // ---- the CLI is the same engines, from a terminal --------------------------------------------------------------
private:
    QJsonDocument cli(const QStringList &args, int *rc = nullptr, const QString &dataDir = QString())
    {
        QProcess p;
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert(u"TAOS_SURVIVAL_DIR"_s, dataDir.isEmpty() ? path(u"clidata"_s) : dataDir);
        env.insert(u"QT_QPA_PLATFORM"_s, u"offscreen"_s);
        p.setProcessEnvironment(env);
        p.start(QStringLiteral(SURVIVAL_CLI), args);
        p.waitForFinished(30000);
        if (rc) *rc = p.exitCode();
        return QJsonDocument::fromJson(p.readAllStandardOutput());
    }
private Q_SLOTS:
    void cli_drives_packs_library_map_radios_kits_and_markers()
    {
        const QString data = path(u"clidata"_s);
        QDir().mkpath(data + u"/packs"_s);
        QVERIFY(Fixture::writeZim(data + u"/packs/lib.zim"_s));
        QVERIFY(Fixture::writePmtiles(data + u"/packs/area.pmtiles"_s));
        int rc = 0;
        auto packs = cli({u"packs"_s}, &rc);
        QCOMPARE(rc, 0);
        QStringList ids;
        for (const QJsonValue &v : packs.array()) ids << v.toObject()[u"id"_s].toString();
        QVERIFY(ids.contains(u"local-lib"_s) && ids.contains(u"local-area"_s));
        auto hits = cli({u"library"_s, u"search"_s, u"local-lib"_s, u"bacteria"_s}, &rc);
        QCOMPARE(rc, 0);
        QCOMPARE(hits.array()[0].toObject()[u"path"_s].toString(), u"water"_s);
        QProcess art;
        art.setProcessEnvironment([&] { auto e = QProcessEnvironment::systemEnvironment(); e.insert(u"TAOS_SURVIVAL_DIR"_s, data); return e; }());
        art.start(QStringLiteral(SURVIVAL_CLI), {u"library"_s, u"article"_s, u"local-lib"_s, u"water"_s});
        art.waitForFinished(30000);
        const QString html = QString::fromUtf8(art.readAllStandardOutput());
        QVERIFY(html.contains(u"Boil the water"_s));
        QVERIFY(!html.contains(u"alert"_s) && !html.contains(u"evil"_s));
        auto places = cli({u"map"_s, u"search"_s, u"local-area"_s, u"testv"_s}, &rc);
        QCOMPARE(rc, 0);
        QCOMPARE(places.array().size(), 1);
        QVERIFY(cli({u"radios"_s}, &rc).isArray());
        QCOMPARE(rc, 0);
        QVERIFY(cli({u"kits"_s}, &rc).array().size() >= 4);
        QProcess add;
        add.setProcessEnvironment([&] { auto e = QProcessEnvironment::systemEnvironment(); e.insert(u"TAOS_SURVIVAL_DIR"_s, data); return e; }());
        add.start(QStringLiteral(SURVIVAL_CLI), {u"markers"_s, u"add"_s, u"Spring"_s, u"-4.2"_s, u"55.8"_s});
        add.waitForFinished(30000);
        QCOMPARE(add.exitCode(), 0);
        QCOMPARE(cli({u"markers"_s}).array().size(), 1);
        // delete through the CLI removes the file
        cli({u"packs"_s, u"remove"_s, u"local-lib"_s}, &rc);
        QCOMPARE(rc, 0);
        QVERIFY(!QFile::exists(data + u"/packs/lib.zim"_s));
        // a bad checksum on import refuses and exits non-zero
        writeFile(path(u"imp2.zim"_s), "zz");
        cli({u"packs"_s, u"import"_s, path(u"imp2.zim"_s), QString(64, QLatin1Char('0'))}, &rc);
        QVERIFY(rc != 0);
        QVERIFY(!QFile::exists(data + u"/packs/imp2.zim"_s));
    }
};

QTEST_MAIN(TstSurvivalEngine)
#include "tst_survival_engine.moc"
