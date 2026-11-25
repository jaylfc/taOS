// SPDX-License-Identifier: AGPL-3.0-or-later
// The container apps engine: manifest rules, the CLI with a fake incus (install, refuse, remove keeps data, listing
// shows only your groups), launcher entry, exit codes. No real container is touched.
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>

#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>

#include "appgroups.h"
#include "manifest.h"
#include "owner.h"
#include "callbackbroker.h"

#include <memory>
#include <thread>
#include <pwd.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <netinet/in.h>
#include <arpa/inet.h>

using namespace Qt::StringLiterals;
using namespace TaosAppGroups;

namespace {
QByteArray readAll(const QString &p)
{
    QFile f(p);
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}
void writeFile(const QString &p, const QByteArray &d)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(d);
}
} // namespace

class TstAppGroups : public QObject
{
    Q_OBJECT
    QTemporaryDir m_tmp;
    QString fakeDir, dataDir, xdgData, catDir;

    struct Run { int code; QJsonObject last; QString err; QString raw; };
    std::unique_ptr<OwnerGate> gate;   // stands in for the Store: the only thing that can mint a confirmation
    QString runDir;
    // The marker "--test-owner-ok <app-id>" in the arguments means "the person pressed the Store's accept button for
    // that app": a real nonce is minted by the gate and handed to the CLI the way the Store does. Without the marker the
    // CLI gets nothing, exactly like an agent with only a command line.
    Run cli(QStringList args, QMap<QString, QString> env = {})
    {
        const int mk = int(args.indexOf(u"--test-owner-ok"_s));
        if (mk >= 0 && mk + 1 < args.size()) {
            env.insert(u"TAOS_OWNER_NONCE"_s, gate->mint(QLatin1String(kActionLicence), args[mk + 1]));
            args.remove(mk, 2);
        }
        const int mu = int(args.indexOf(u"--test-owner-update"_s));
        if (mu >= 0 && mu + 1 < args.size()) {
            env.insert(u"TAOS_OWNER_NONCE"_s, gate->mint(QLatin1String(kActionUpdate), args[mu + 1]));
            args.remove(mu, 2);
        }
        QProcess p;
        QProcessEnvironment e = QProcessEnvironment::systemEnvironment();
        e.insert(u"TAOS_APPGROUPS_OWNER_DIR"_s, runDir);
        e.remove(u"TAOS_OWNER_NONCE"_s);
        e.insert(u"TAOS_APPGROUPS_INCUS"_s, QStringLiteral(FAKE_INCUS));
        e.insert(u"FAKE_DIR"_s, fakeDir);
        e.insert(u"FAKE_OWNER"_s, QString::number(::getuid()));
        e.insert(u"TAOS_APPGROUPS_DATA"_s, dataDir);
        e.insert(u"XDG_DATA_HOME"_s, xdgData);
        e.insert(u"HOME"_s, m_tmp.path() + u"/home"_s);
        e.insert(u"TAOS_APPGROUPS_EXEC"_s, u"/stable/bin/taos-appgroups"_s);
        e.insert(u"INCUS_SOCKET"_s, u"/nonexistent"_s);
        e.insert(u"TAOS_APPGROUPS_DRI_DIR"_s, m_tmp.path() + u"/dri"_s);
        e.insert(u"FAKE_SHA"_s, u"7baa36baab285cb46101af958b8f7be6305458db9a44d8b3d0938d61f38597d7"_s);
        e.insert(u"FAKE_FPR"_s, u"35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3"_s);
        for (auto it = env.begin(); it != env.end(); ++it)
            e.insert(it.key(), it.value());
        p.setProcessEnvironment(e);
        p.start(QStringLiteral(APPGROUPS_CLI), args);
        p.waitForFinished(30000);
        Run r;
        r.code = p.exitCode();
        r.raw = QString::fromUtf8(p.readAllStandardOutput());
        r.err = QString::fromUtf8(p.readAllStandardError());
        const QStringList lines = r.raw.trimmed().split(u'\n');
        r.last = QJsonDocument::fromJson(lines.last().toUtf8()).object();
        return r;
    }
    void setList(const QString &owner)
    {
        const QString j = u"[{\"name\":\"user-apps\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"default\",\"user.taos.owner\":\"%1\"},\"devices\":{}},"
                          u"{\"name\":\"appgroup-dev-apps\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"dev-apps\",\"user.taos.owner\":\"%1\"},\"devices\":{}},"
                          u"{\"name\":\"appgroup-theirs\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"theirs\",\"user.taos.owner\":\"4242\"},\"devices\":{}}]"_s.arg(owner);
        writeFile(fakeDir + u"/list.json"_s, j.toUtf8());
    }

private slots:
    void initTestCase()
    {
        fakeDir = m_tmp.path() + u"/fake"_s;
        dataDir = m_tmp.path() + u"/data"_s;
        xdgData = m_tmp.path() + u"/xdg"_s;
        catDir = m_tmp.path() + u"/cat"_s;
        QDir().mkpath(fakeDir);
        runDir = m_tmp.path() + u"/run"_s;
        qputenv("TAOS_APPGROUPS_OWNER_DIR", runDir.toUtf8());
        gate = std::make_unique<OwnerGate>();
        QString gerr;
        QVERIFY2(gate->start(&gerr), qPrintable(gerr));
        QDir().mkpath(m_tmp.path() + u"/home"_s);
        // a GPU with two render nodes and a card node: the lowest render node is granted, never the card
        QDir().mkpath(m_tmp.path() + u"/dri"_s);
        for (const char *n : {"card0", "renderD129", "renderD130"})
            writeFile(m_tmp.path() + u"/dri/"_s + QLatin1String(n), "x");
    }
    void cleanupTestCase() { gate.reset(); }
    void init()
    {
        QFile::remove(fakeDir + u"/calls.log"_s);
        QDir(dataDir).removeRecursively();
        QDir(xdgData).removeRecursively();
        setList(QString::number(::getuid()));
    }

    void manifestAntigravityIsValid()
    {
        Manifest m;
        QString err;
        QVERIFY2(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s), &m, &err), qPrintable(err));
        QCOMPARE(m.grants.network, true);
        QCOMPARE(m.grants.camera, false);
        QVERIFY(m.recipe.size() >= 2);
    }
    void manifestRulesBite_data()
    {
        QTest::addColumn<QString>("from");
        QTest::addColumn<QString>("to");
        QTest::addColumn<QString>("expect");
        QTest::newRow("http url") << u"\"url\": \"https://edgedl"_s << u"\"url\": \"http://edgedl"_s << u"https"_s;
        QTest::newRow("foreign host") << u"edgedl.me.gvt1.com"_s << u"evil.example.com"_s << u"allowlist"_s;
        QTest::newRow("no checksum") << u"\"sha256\": \"7baa"_s << u"\"sha256\": \"zz"_s << u"sha256"_s;
        QTest::newRow("shell in package") << u"\"git\""_s << u"\"git; rm -rf /\""_s << u"not allowed"_s;
        QTest::newRow("unknown op") << u"\"op\": \"symlink\""_s << u"\"op\": \"shell\""_s << u"unknown recipe step"_s;
        QTest::newRow("unknown grant") << u"\"documents\": true"_s << u"\"root\": true"_s << u"unknown grant"_s;
        QTest::newRow("dest outside opt") << u"\"dest\": \"/opt/antigravity\""_s << u"\"dest\": \"/etc\""_s << u"dest"_s;
    }
    void manifestRulesBite()
    {
        QFETCH(QString, from);
        QFETCH(QString, to);
        QFETCH(QString, expect);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s));
        QVERIFY(text.contains(from));
        text.replace(from, to);
        Manifest m;
        QString err;
        QVERIFY(!parseManifest(text.toUtf8(), &m, &err));
        QVERIFY2(err.contains(expect), qPrintable(err));
    }
    void installWritesLauncherAndRecord()
    {
        const Run r = cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--json"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QCOMPARE(r.last.value(u"desktop_id"_s).toString(), u"taos-appgroups-antigravity"_s);
        const QString d = QString::fromUtf8(readAll(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QVERIFY(d.contains(u"Exec=/stable/bin/taos-appgroups run antigravity %U"_s));
        QVERIFY(d.contains(u"StartupWMClass=antigravity-ide"_s));
        QVERIFY(d.contains(u"Icon=/"_s));
        QVERIFY(QFileInfo::exists(dataDir + u"/icons/antigravity.svg"_s));
        const QString calls = QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s));
        QVERIFY(calls.contains(u"apt-get install -y --no-install-recommends libgtk-3-0"_s));
        QVERIFY(calls.contains(u"tar -xzf /var/tmp/taos-dl-antigravity -C /opt/antigravity --strip-components=1"_s));
        QVERIFY(!calls.contains(u"--no-sandbox"_s));
        QVERIFY(!calls.contains(u"security.privileged=true"_s));
        QVERIFY(calls.contains(u"gpu unix-char source="_s + m_tmp.path() + u"/dri/renderD129 path=/dev/dri/renderD129"_s));
        QVERIFY(!calls.contains(u"renderD130"_s));
        QVERIFY(!calls.contains(u"card0"_s));
        // Every event line is one JSON object (the Store parses them).
        for (const QString &l : r.raw.trimmed().split(u'\n'))
            QVERIFY2(!QJsonDocument::fromJson(l.toUtf8()).isNull(), qPrintable(l));
        const Run again = cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s});
        QCOMPARE(again.code, 7);
    }
    void wrongChecksumIsRefusedAndLeavesNothing()
    {
        const Run r = cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}, {{u"FAKE_SHA"_s, u"00"_s}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"pinned checksum"_s), qPrintable(r.err));
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QCOMPARE(cli({u"list"_s, u"--json"_s}).last.value(u"apps"_s).toArray().size(), 0);
    }
    void noRenderNodeSkipsTheGpuGrantWithAMessage()
    {
        const QString empty = m_tmp.path() + u"/nodri"_s;
        QDir().mkpath(empty);
        const Run r = cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}, {{u"TAOS_APPGROUPS_DRI_DIR"_s, empty}});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY2(r.err.contains(u"no GPU render node"_s), qPrintable(r.err));
        QVERIFY(!QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s)).contains(u"unix-char"_s));
    }
    void launcherTextFromTheManifestCannotAddKeys()
    {
        QDir().mkpath(catDir);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s));
        text.replace(u"\"id\": \"antigravity\""_s, u"\"id\": \"nl\""_s)
            .replace(u"\"name\": \"Antigravity\""_s, u"\"name\": \"Evil\\nExec=/bin/false\\rX-Bad=1\""_s)
            .replace(u"\"description\": \"Google's"_s, u"\"description\": \"line one\\nHidden=true\\n Google's"_s)
            .replace(u"/opt/antigravity"_s, u"/opt/nl"_s);
        writeFile(catDir + u"/nl.json"_s, text.toUtf8());
        const Run r = cli({u"install"_s, u"nl"_s, u"--test-owner-ok"_s, u"nl"_s}, {{u"TAOS_APPGROUPS_CATALOG"_s, catDir}});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QString d = QString::fromUtf8(readAll(xdgData + u"/applications/taos-appgroups-nl.desktop"_s));
        QVERIFY(d.contains(u"Name=Evil Exec=/bin/false X-Bad=1\n"_s));
        QVERIFY(d.contains(u"Comment=line one Hidden=true Google's"_s));
        QCOMPARE(d.count(u"\nExec="_s), 1);
        QVERIFY(!d.contains(u"\nHidden="_s));
        QVERIFY(!d.contains(u"\nX-Bad="_s));
    }
    void wrongKeyFingerprintIsRefused()
    {
        QDir().mkpath(catDir);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s));
        text.replace(u"\"id\": \"antigravity\""_s, u"\"id\": \"keyed\""_s)
            .replace(u"\"op\": \"symlink\""_s, u"\"op\": \"apt-source\", \"name\": \"keyed\", \"uri\": \"https://us-central1-apt.pkg.dev/x/\", \"suite\": \"s\", \"components\": [\"main\"], \"key_url\": \"https://us-central1-apt.pkg.dev/k.gpg\", \"key_fingerprint\": \"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\" }, {\"op\": \"symlink\""_s)
            .replace(u"/opt/antigravity"_s, u"/opt/keyed"_s);
        writeFile(catDir + u"/keyed.json"_s, text.toUtf8());
        const Run r = cli({u"install"_s, u"keyed"_s, u"--test-owner-ok"_s, u"keyed"_s}, {{u"TAOS_APPGROUPS_CATALOG"_s, catDir}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"expected fingerprint"_s), qPrintable(r.err));
    }
    void refusedManifestExitsFive()
    {
        QDir().mkpath(catDir);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s));
        text.replace(u"https://edgedl"_s, u"http://edgedl"_s).replace(u"\"id\": \"antigravity\""_s, u"\"id\": \"bad\""_s);
        writeFile(catDir + u"/bad.json"_s, text.toUtf8());
        QCOMPARE(cli({u"install"_s, u"bad"_s}, {{u"TAOS_APPGROUPS_CATALOG"_s, catDir}}).code, 5);
        QCOMPARE(cli({u"install"_s, u"nosuchapp"_s}).code, 3);
    }
    void removeKeepsDataUnlessAsked()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"remove"_s, u"antigravity"_s, u"--json"_s});
        QCOMPARE(r.code, 0);
        QCOMPARE(r.last.value(u"data_kept"_s).toBool(), true);
        const QString calls = QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s));
        QVERIFY(calls.contains(u"apt-mark auto libgtk-3-0"_s));
        QVERIFY(calls.contains(u"apt-get autoremove -y --purge"_s));
        QVERIFY(!calls.contains(u"apt-get remove"_s));
        QVERIFY(!calls.contains(u"rm -rf /home/taos/apps/antigravity"_s));
        // The base graphics libraries are never removed with an app (a removal once took libgbm1 and Mesa EGL with it).
        QVERIFY(!calls.contains(u"libgbm1"_s));
        QVERIFY(!calls.contains(u"libxkbcommon0"_s));
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"remove"_s, u"antigravity"_s, u"--purge-data"_s}).code, 0);
        QVERIFY(QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s)).contains(u"rm -rf /home/taos/apps/antigravity"_s));
    }
    void groupsShowOnlyYourOwn()
    {
        const Run r = cli({u"groups"_s, u"--json"_s});
        QCOMPARE(r.code, 0);
        const QJsonArray g = r.last.value(u"groups"_s).toArray();
        QCOMPARE(g.size(), 2);   // user-apps and appgroup-dev-apps; the other account's group is not shown
        QCOMPARE(g[0].toObject().value(u"container"_s).toString(), u"user-apps"_s);
        QCOMPARE(g[0].toObject().value(u"display_name"_s).toString(), u"My apps"_s);
        QCOMPARE(g[1].toObject().value(u"container"_s).toString(), u"appgroup-dev-apps"_s);
        QCOMPARE(g[1].toObject().value(u"display_name"_s).toString(), u"Dev apps"_s);
        const QString st = cli({u"status"_s, u"--json"_s}).raw;
        QVERIFY(!st.contains(u"appgroup-theirs"_s));
    }
    void targetsHostAndWorkerAreNotSilentlyFallenBack()
    {
        const Run h = cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--target"_s, u"host"_s, u"--accept-host-warning"_s, u"--json"_s});
        QCOMPARE(h.code, 9);
        QVERIFY(h.last.value(u"error"_s).toString().contains(u"not available yet"_s));
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--target"_s, u"worker:desk2"_s}).code, 9);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--target"_s, u"bogus"_s}).code, 2);
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QVERIFY(!QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s)).contains(u"exec"_s));
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--target"_s, u"group:default"_s}).code, 0);
    }

    // ---------------------------------------------------------------- schema 2, licence, new manifests

    static QString theAccount() { return QString::fromLocal8Bit(::getpwuid(::geteuid())->pw_name); }
    QString callsLog() { return QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s)); }
    QJsonObject indexRec(const QString &id)
    {
        return QJsonDocument::fromJson(readAll(dataDir + u"/installed.json"_s)).object().value(u"apps"_s).toObject().value(id).toObject();
    }
    static QMap<QString, QString> claudeEnv() { return {{u"FAKE_FPR"_s, u"31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"_s}}; }
    static QMap<QString, QString> kiloEnv() { return {{u"FAKE_SHA"_s, u"5d9d8b00ac958cba8722bce54eefc502a6fa092a5dad27cf34984800ed388b81"_s}}; }

    void newManifestsAreValidSchema2()
    {
        for (const QString &id : {u"claude-desktop"_s, u"kilo-desktop"_s}) {
            Manifest m;
            QString err;
            QVERIFY2(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/"_s + id + u".json"_s), &m, &err), qPrintable(err));
            QCOMPARE(m.schema, 2);
            QVERIFY(m.licenceRequired);
            QVERIFY(m.licenceUrl.startsWith(u"https://"_s));
            QVERIFY(!m.licence.isEmpty());
            QVERIFY(m.grants.network && m.grants.gpu && m.grants.documents && m.grants.downloads);
            QVERIFY(!m.grants.camera && !m.grants.microphone);
            QVERIFY(m.data.isEmpty());   // the whole app home moves: nothing the app wrote is left behind
        }
        Manifest c;
        QString err;
        QVERIFY(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/claude-desktop.json"_s), &c, &err));
        QCOMPARE(c.recipe[1].op, u"apt-source"_s);   // Anthropic's own apt repository, pinned key
        QCOMPARE(c.recipe[1].args.value(u"key_fingerprint"_s).toString(), u"31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"_s);
        Manifest k;
        QVERIFY(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/kilo-desktop.json"_s), &k, &err));
        QCOMPARE(k.recipe[0].op, u"download-deb"_s);
        QVERIFY(!k.updateFeedUrl.isEmpty());
    }
    void schema1ManifestsStillLoadWithoutAcceptance()
    {
        Manifest m;
        QString err;
        QVERIFY(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/chromium.json"_s), &m, &err));
        QCOMPARE(m.schema, 1);
        QVERIFY(!m.licenceRequired);
        QVERIFY(m.data.isEmpty());   // no data field: the whole app home
        QVERIFY(m.licenceUrl.isEmpty());
    }
    void schema2RulesBite_data()
    {
        QTest::addColumn<QString>("from");
        QTest::addColumn<QString>("to");
        QTest::addColumn<QString>("expect");
        QTest::newRow("licence not https") << u"\"url\": \"https://www.anthropic.com/legal/consumer-terms\",\n    \"accept\""_s
                                           << u"\"url\": \"http://www.anthropic.com/x\",\n    \"accept\""_s << u"licence.url"_s;
        QTest::newRow("bad accept") << u"\"accept\": \"required\""_s << u"\"accept\": \"maybe\""_s << u"licence.accept"_s;
        QTest::newRow("data escapes home") << u"\"data\": [\".claude\"]"_s << u"\"data\": [\"../etc\"]"_s << u"data paths"_s;
        QTest::newRow("data absolute") << u"\"data\": [\".claude\"]"_s << u"\"data\": [\"/etc\"]"_s << u"data paths"_s;
        QTest::newRow("etc-default value") << u"\"false\"}"_s << u"\"false; rm\"}"_s << u"etc-default"_s;
        QTest::newRow("apt arch") << u"\"amd64 arm64\""_s << u"\"amd64\\nExec=x\""_s << u"arch"_s;
        QTest::newRow("foreign key host") << u"https://downloads.claude.ai/claude-desktop/key.asc"_s << u"https://evil.example.com/key.asc"_s << u"allowlist"_s;
        QTest::newRow("schema 3") << u"\"schema\": 2"_s << u"\"schema\": 3"_s << u"schema 1 or 2"_s;
    }
    void schema2RulesBite()
    {
        QFETCH(QString, from);
        QFETCH(QString, to);
        QFETCH(QString, expect);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/claude-desktop.json"_s));
        text.replace(u"\"binary\":"_s, u"\"data\": [\".claude\"],\n  \"binary\":"_s);
        QVERIFY2(text.contains(from), qPrintable(from));
        text.replace(from, to);
        Manifest m;
        QString err;
        QVERIFY(!parseManifest(text.toUtf8(), &m, &err));
        QVERIFY2(err.contains(expect), qPrintable(err));
    }
    void kiloDebWithoutChecksumOrPackageIsRefused()
    {
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/kilo-desktop.json"_s));
        Manifest m;
        QString err;
        QString a = text;
        a.replace(u"\"sha256\": \"5d9d"_s, u"\"sha256\": \"zz"_s);
        QVERIFY(!parseManifest(a.toUtf8(), &m, &err));
        QVERIFY2(err.contains(u"sha256"_s), qPrintable(err));
        QString b = text;
        b.replace(u"\"package\": \"kilo-desktop\", \"url\""_s, u"\"url\""_s);
        QVERIFY(!parseManifest(b.toUtf8(), &m, &err));
        QVERIFY2(err.contains(u"package name"_s), qPrintable(err));
    }
    void installNeedsLicenceAcceptanceAndRecordsIt()
    {
        const Run no = cli({u"install"_s, u"claude-desktop"_s, u"--json"_s}, claudeEnv());
        QCOMPARE(no.code, 10);
        QCOMPARE(no.last.value(u"licence"_s).toObject().value(u"url"_s).toString(), u"https://www.anthropic.com/legal/consumer-terms"_s);
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-claude-desktop.desktop"_s));
        QVERIFY2(!callsLog().contains(u"apt-get"_s), "nothing is installed before the licence is accepted");
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}, claudeEnv()).code, 2);   // wrong id
        const Run ok = cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s, u"--accepted-by"_s, u"someone else entirely"_s, u"--json"_s}, claudeEnv());
        QVERIFY2(ok.code == 0, qPrintable(ok.raw + ok.err));
        const QJsonObject acc = indexRec(u"claude-desktop"_s).value(u"licence_accepted"_s).toObject();
        QCOMPARE(acc.value(u"url"_s).toString(), u"https://www.anthropic.com/legal/consumer-terms"_s);
        QCOMPARE(acc.value(u"name"_s).toString(), u"Anthropic Consumer Terms of Service"_s);
        QCOMPARE(acc.value(u"accepted_by"_s).toString(), theAccount() + u" (confirmed in the Store)"_s);   // from the Store's session, never the flag
        QVERIFY(QDateTime::fromString(acc.value(u"accepted_at"_s).toString(), Qt::ISODate).isValid());
        const QString calls = callsLog();
        QVERIFY(calls.contains(u"tee /etc/default/claude-desktop"_s));
        QVERIFY(calls.contains(u"tee /etc/apt/sources.list.d/claude-desktop.sources"_s));
        QVERIFY(calls.contains(u"apt-get install -y --no-install-recommends claude-desktop libasound2t64"_s));
        QVERIFY(calls.contains(u"dpkg-query"_s));
        QCOMPARE(indexRec(u"claude-desktop"_s).value(u"installed_version"_s).toString(), u"1.0.0"_s);
        const QString d = QString::fromUtf8(readAll(xdgData + u"/applications/taos-appgroups-claude-desktop.desktop"_s));
        QVERIFY(d.contains(u"StartupWMClass=com.anthropic.Claude"_s));
    }
    void kiloInstallsOnlyWithItsPinnedChecksum()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}).code, 6);   // default FAKE_SHA is not Kilo's
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-kilo-desktop.desktop"_s));
        const Run r = cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv());
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY(callsLog().contains(u"apt-get install -y /var/tmp/taos-dl-kilo-desktop.deb"_s));
    }
    void removingADebAppRemovesItsPackage()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"remove"_s, u"kilo-desktop"_s}).code, 0);
        QVERIFY2(callsLog().contains(u"apt-get remove -y --purge kilo-desktop"_s), qPrintable(callsLog()));
    }

    // ---------------------------------------------------------------- groups: display names

    void groupCreateStoresADisplayName()
    {
        const Run r = cli({u"group"_s, u"create"_s, u"internet-apps"_s, u"--display-name"_s, u"Internet apps"_s, u"--json"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY2(callsLog().contains(u"config set appgroup-internet-apps user.taos.display_name=Internet apps"_s), qPrintable(callsLog()));
        QCOMPARE(cli({u"group"_s, u"create"_s, u"x"_s, u"--display-name"_s, u"bad\nname"_s}).code, 2);
    }
    void displayNameComesFromTheContainerThenFromTheName()
    {
        const QString j = u"[{\"name\":\"appgroup-internet-apps\",\"status\":\"Running\",\"config\":{\"user.taos.kind\":\"app-group\",\"user.taos.group\":\"internet-apps\",\"user.taos.display_name\":\"Web and mail\",\"user.taos.owner\":\"%1\"},\"devices\":{}}]"_s.arg(::getuid());
        writeFile(fakeDir + u"/list.json"_s, j.toUtf8());
        QCOMPARE(cli({u"groups"_s, u"--json"_s}).last.value(u"groups"_s).toArray()[0].toObject().value(u"display_name"_s).toString(), u"Web and mail"_s);
    }

    // ---------------------------------------------------------------- move

    int firstCall(const QString &needle, const QString &calls) { return calls.indexOf(needle); }
    // a call of `incus exec <ct> [--env X ...] -- <cmd...>`
    static bool execCall(const QString &calls, const QString &ct, const QString &cmd)
    {
        return QRegularExpression(u"exec %1( --env \\S+)* -- %2"_s.arg(QRegularExpression::escape(ct), QRegularExpression::escape(cmd))).match(calls).hasMatch();
    }
    static int execPos(const QString &calls, const QString &ct, const QString &cmd)
    {
        return QRegularExpression(u"exec %1( --env \\S+)* -- %2"_s.arg(QRegularExpression::escape(ct), QRegularExpression::escape(cmd))).match(calls).capturedStart();
    }

    void moveInstallsCopiesVerifiesSwitchesThenBacksUp()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s, u"--json"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        for (const QString &l : r.raw.trimmed().split(u'\n'))
            QVERIFY2(!QJsonDocument::fromJson(l.toUtf8()).isNull(), qPrintable(l));
        QCOMPARE(r.last.value(u"group"_s).toString(), u"dev-apps"_s);
        QCOMPARE(r.last.value(u"from"_s).toString(), u"default"_s);
        const QString backup = r.last.value(u"backup"_s).toString();
        QVERIFY2(QRegularExpression(u"^/home/taos/apps/\\.backup/antigravity-\\d{8}-\\d{6}$"_s).match(backup).hasMatch(), qPrintable(backup));
        const QJsonObject rec = indexRec(u"antigravity"_s);
        QCOMPARE(rec.value(u"group"_s).toString(), u"dev-apps"_s);
        QCOMPARE(rec.value(u"previous_group"_s).toString(), u"default"_s);
        QCOMPARE(QJsonDocument::fromJson(readAll(dataDir + u"/installed.json"_s)).object().value(u"backups"_s).toArray().size(), 1);
        const QString d = QString::fromUtf8(readAll(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QVERIFY2(d.contains(u"X-taOS-AppGroup=dev-apps"_s), qPrintable(d));
        const QString c = callsLog();
        const int inst = firstCall(u"exec appgroup-dev-apps -- tar -xzf /var/tmp/taos-dl-antigravity"_s, c);
        const int out = firstCall(u"exec user-apps -- tar -C /home/taos/apps/antigravity -cf - --numeric-owner"_s, c);
        const int in = firstCall(u"exec appgroup-dev-apps -- tar -C /home/taos/apps/antigravity -xpf - --numeric-owner"_s, c);
        const int bk = firstCall(u".backup/antigravity-"_s, c);
        const int rm = execPos(c, u"user-apps"_s, u"apt-mark auto"_s);
        QVERIFY2(inst >= 0 && out >= 0 && in >= 0 && bk >= 0 && rm >= 0,
                 qPrintable(u"positions inst %1 out %2 in %3 backup %4 remove %5\n"_s.arg(inst).arg(out).arg(in).arg(bk).arg(rm) + QString(c).replace(u'\n', u' ')));
        QVERIFY2(inst < in && inst < out, "installed into the target before the copy");
        QVERIFY2(in < bk && out < bk, "copied before the backup");
        QVERIFY2(in < bk && bk < rm, "data is backed up, then the source app is removed");
        QVERIFY2(!c.contains(u"rm -rf /home/taos/apps/antigravity"_s), "the source data is never deleted, only renamed");
        QVERIFY(c.contains(u"ldd \"$(readlink -f"_s) && c.contains(u"sh /opt/antigravity/antigravity-ide"_s));
        QCOMPARE(cli({u"groups"_s, u"--json"_s}).last.value(u"groups"_s).toArray()[1].toObject().value(u"apps"_s).toArray().size(), 1);
        // moving it back is refused when it is already there
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 7);
    }
    void moveUsesTheManifestDataList()
    {
        QDir().mkpath(catDir);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/claude-desktop.json"_s));
        text.replace(u"\"binary\":"_s, u"\"data\": [\".config/Claude\", \".claude\", \".cache/claude-desktop\"],\n  \"binary\":"_s);
        writeFile(catDir + u"/claude-desktop.json"_s, text.toUtf8());
        QMap<QString, QString> env = claudeEnv();
        env.insert(u"TAOS_APPGROUPS_CATALOG"_s, catDir);
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, env).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"move"_s, u"claude-desktop"_s, u"--to"_s, u"group:dev-apps"_s}, env);
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QString c = callsLog();
        QVERIFY2(c.contains(u"-cf - --numeric-owner .config/Claude .claude .cache/claude-desktop"_s), qPrintable(c));
        QVERIFY(!indexRec(u"claude-desktop"_s).value(u"licence_accepted"_s).toObject().isEmpty());   // acceptance travels with the app
    }
    void moveRefusesARunningApp()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}, {{u"FAKE_RUNNING"_s, u"1"_s}});
        QCOMPARE(r.code, 7);
        QVERIFY2(r.err.contains(u"is running"_s), qPrintable(r.err));
        QVERIFY2(!callsLog().contains(u"appgroup-dev-apps -- apt-get"_s), "nothing was installed in the target");
        QCOMPARE(indexRec(u"antigravity"_s).value(u"group"_s).toString(), u"default"_s);
    }
    void moveRefusalsAreSpecific()
    {
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 3);           // not installed
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:nope"_s}).code, 3);               // no such group
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:default"_s}).code, 7);            // already there
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"host"_s}).code, 9);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s}).code, 2);
        QCOMPARE(indexRec(u"antigravity"_s).value(u"group"_s).toString(), u"default"_s);
    }
    void moveFailuresLeaveTheSourceUntouched_data()
    {
        QTest::addColumn<QString>("knob");
        QTest::addColumn<int>("code");
        QTest::addColumn<bool>("targetTouched");
        QTest::newRow("target home not empty") << u"FAKE_TARGET_HAS_DATA"_s << 7 << false;
        QTest::newRow("copy does not match") << u"FAKE_COUNT_MISMATCH"_s << 6 << true;
        QTest::newRow("library missing") << u"FAKE_LDD_MISSING"_s << 6 << true;
    }
    void moveFailuresLeaveTheSourceUntouched()
    {
        QFETCH(QString, knob);
        QFETCH(int, code);
        QFETCH(bool, targetTouched);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        const QByteArray before = readAll(dataDir + u"/installed.json"_s);
        const QByteArray launcher = readAll(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}, {{knob, u"1"_s}});
        QCOMPARE(r.code, code);
        const QString c = callsLog();
        QCOMPARE(readAll(dataDir + u"/installed.json"_s), before);
        QCOMPARE(readAll(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s), launcher);
        QVERIFY2(!c.contains(u".backup"_s), "the source data was not renamed");
        QVERIFY2(!execCall(c, u"user-apps"_s, u"apt-mark"_s), "the source app was not removed");
        QVERIFY2(!c.contains(u"exec user-apps -- rm"_s), "nothing was deleted in the source");
        QCOMPARE(c.contains(u"appgroup-dev-apps -- tar -xzf"_s), targetTouched);
        if (targetTouched)
            QVERIFY2(c.contains(u"appgroup-dev-apps -- find /home/taos/apps/antigravity -mindepth 1 -maxdepth 1 -exec rm -rf"_s), "the half copy is cleaned from the target");
    }
    void moveFromAnOlderIndexWorks()
    {
        // installed.json as an earlier engine wrote it: no installed_version, no licence_accepted, no backups, no previous_group
        QDir().mkpath(dataDir);
        QJsonObject man = QJsonDocument::fromJson(readAll(QStringLiteral(CATALOG_DIR) + u"/chromium.json"_s)).object();
        QJsonObject rec{{u"group"_s, u"default"_s}, {u"target"_s, QJsonObject{{u"kind"_s, u"group"_s}, {u"id"_s, u"default"_s}}},
                        {u"installed_at"_s, u"2026-11-20T10:00:00Z"_s}, {u"manifest"_s, man}};
        writeFile(dataDir + u"/installed.json"_s, QJsonDocument(QJsonObject{{u"apps"_s, QJsonObject{{u"chromium"_s, rec}}}}).toJson());
        QCOMPARE(cli({u"list"_s, u"--json"_s}).last.value(u"apps"_s).toArray().size(), 1);
        QCOMPARE(cli({u"launchers"_s, u"sync"_s}).code, 0);
        const Run r = cli({u"move"_s, u"chromium"_s, u"--to"_s, u"group:dev-apps"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QCOMPARE(indexRec(u"chromium"_s).value(u"group"_s).toString(), u"dev-apps"_s);
        QCOMPARE(indexRec(u"chromium"_s).value(u"installed_at"_s).toString(), u"2026-11-20T10:00:00Z"_s);
        const Run b = cli({u"backups"_s, u"--json"_s});
        QCOMPARE(b.last.value(u"backups"_s).toArray().size(), 1);
    }
    void backupsAreListedAndDeletedOnlyOnRequest()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 0);
        const QJsonObject b = cli({u"backups"_s, u"--json"_s}).last.value(u"backups"_s).toArray()[0].toObject();
        QCOMPARE(b.value(u"app"_s).toString(), u"antigravity"_s);
        QCOMPARE(b.value(u"group"_s).toString(), u"default"_s);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"backups"_s, u"delete"_s, u"antigravity"_s, b.value(u"stamp"_s).toString(), u"--json"_s}).code, 0);
        QVERIFY(callsLog().contains(u"rm -rf /home/taos/apps/.backup/antigravity-"_s));
        QCOMPARE(cli({u"backups"_s, u"--json"_s}).last.value(u"backups"_s).toArray().size(), 0);
        QCOMPARE(cli({u"backups"_s, u"delete"_s, u"antigravity"_s, u"../../etc"_s}).code, 2);
    }

    // ---------------------------------------------------------------- update

    static QString feed(const QString &version, const QString &sha)
    {
        return u"{\"schemaVersion\":1,\"version\":\"%1\",\"downloads\":[{\"platform\":\"linux\",\"arch\":\"x64\",\"type\":\"deb\",\"url\":\"https://autoupdate.desktop.anaconda.com/kilo-desktop/latest/linux/x64/kilo-desktop_%1_amd64.deb\",\"sha256\":\"%2\"}]}"_s.arg(version, sha);
    }
    void updateCheckReadsTheVendorFeed()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        const QString newSha = u"aa"_s.repeated(32);
        Run r = cli({u"update"_s, u"kilo-desktop"_s, u"--check"_s, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}});
        QCOMPARE(r.code, 0);
        QCOMPARE(r.last.value(u"update_available"_s).toBool(), true);
        QCOMPARE(r.last.value(u"latest_version"_s).toString(), u"1.1.0"_s);
        r = cli({u"update"_s, u"kilo-desktop"_s, u"--check"_s, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.0.0"_s, u"5d9d8b00ac958cba8722bce54eefc502a6fa092a5dad27cf34984800ed388b81"_s)}});
        QCOMPARE(r.last.value(u"update_available"_s).toBool(), false);
        // an unreadable feed is unknown, never "up to date"
        r = cli({u"update"_s, u"kilo-desktop"_s, u"--check"_s, u"--json"_s}, {{u"FAKE_FEED"_s, u"not json"_s}});
        QVERIFY(r.code != 0);
    }
    void updateInstallsTheFeedsDebOnlyWithTheFeedsChecksum()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        const QString newSha = u"aa"_s.repeated(32);
        QFile::remove(fakeDir + u"/calls.log"_s);
        // the download's checksum is not the feed's: refused, nothing installed
        Run r = cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.1.0#"_s + newSha}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"does not match"_s), qPrintable(r.err));
        QVERIFY(!callsLog().contains(u"apt-get install -y /var/tmp"_s));
        // matching checksum and a stopped app: installed, version recorded
        QFile::remove(fakeDir + u"/calls.log"_s);
        r = cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.1.0#"_s + newSha, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}, {u"FAKE_SHA"_s, newSha}, {u"FAKE_VERSION"_s, u"1.1.0"_s}});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY(callsLog().contains(u"kilo-desktop_1.1.0_amd64.deb"_s));
        QCOMPARE(indexRec(u"kilo-desktop"_s).value(u"installed_version"_s).toString(), u"1.1.0"_s);
        // a running app is never replaced
        r = cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.2.0#"_s + u"bb"_s.repeated(32)}, {{u"FAKE_FEED"_s, feed(u"1.2.0"_s, u"bb"_s.repeated(32))}, {u"FAKE_RUNNING"_s, u"1"_s}});
        QCOMPARE(r.code, 7);
    }
    void updateOfAnAptAppUpgradesOnlyItsPackages()
    {
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, claudeEnv()).code, 0);
        Run r = cli({u"update"_s, u"claude-desktop"_s, u"--check"_s, u"--json"_s}, [&] { auto e = claudeEnv(); e.insert(u"FAKE_CANDIDATE"_s, u"2.0.0"_s); return e; }());
        QCOMPARE(r.last.value(u"update_available"_s).toBool(), true);
        QCOMPARE(r.last.value(u"latest_version"_s).toString(), u"2.0.0"_s);
        QFile::remove(fakeDir + u"/calls.log"_s);
        r = cli({u"update"_s, u"claude-desktop"_s, u"--json"_s}, [&] { auto e = claudeEnv(); e.insert(u"FAKE_CANDIDATE"_s, u"2.0.0"_s); e.insert(u"FAKE_VERSION_AFTER"_s, u"2.0.0"_s); return e; }());
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QString c = callsLog();
        QVERIFY(c.contains(u"apt-get install -y --no-install-recommends --only-upgrade claude-desktop"_s));
        QVERIFY(!c.contains(u"dist-upgrade"_s) && !c.contains(u"apt-get upgrade"_s));
        QCOMPARE(indexRec(u"claude-desktop"_s).value(u"installed_version"_s).toString(), u"2.0.0"_s);
        QCOMPARE(cli({u"update"_s, u"claude-desktop"_s}, {{u"FAKE_RUNNING"_s, u"1"_s}}).code, 7);
    }

    // ---------------------------------------------------------------- icons: the package's own, never reverted

    QString iconLine(const QString &id)
    {
        for (const QString &l : QString::fromUtf8(readAll(xdgData + u"/applications/taos-appgroups-"_s + id + u".desktop"_s)).split(u'\n'))
            if (l.startsWith(u"Icon="_s))
                return l.mid(5);
        return {};
    }
    QString iconsDir() { return dataDir + u"/icons"_s; }

    void installTakesTheIconFromThePackage()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), iconsDir() + u"/kilo-desktop.pkg.png"_s);
        QVERIFY(readAll(iconsDir() + u"/kilo-desktop.pkg.png"_s).startsWith("\x89PNG"));
        QVERIFY(callsLog().contains(u"file pull user-apps/usr/share/icons/hicolor/256x256/apps/found.png"_s));
        // a manifest can name the file inside the container (a tarball has no .desktop file)
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QVERIFY2(callsLog().contains(u"file pull user-apps/opt/antigravity/resources/app/resources/linux/code.png"_s), qPrintable(callsLog()));
        QCOMPARE(iconLine(u"antigravity"_s), iconsDir() + u"/antigravity.pkg.png"_s);
    }
    void aPackageWithoutAnIconKeepsTheGenericOne()
    {
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, {{u"FAKE_NO_ICON"_s, u"1"_s}, {u"FAKE_FPR"_s, u"31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"_s}}).code, 0);
        QCOMPARE(iconLine(u"claude-desktop"_s), iconsDir() + u"/claude-desktop.svg"_s);
        QVERIFY(QFileInfo::exists(iconsDir() + u"/claude-desktop.svg"_s));
    }
    void aHandSetIconSurvivesInstallMoveUpdateAndSync()
    {
        // the state a person left: icons/<id>.png, and a launcher that points at a differently named file
        writeFile(iconsDir() + u"/kilo-desktop.png"_s, "\x89PNG-by-hand");
        writeFile(iconsDir() + u"/antigravity-nobg.png"_s, "\x89PNG-nobg");
        const QString old = u"[Desktop Entry]\nType=Application\nName=Antigravity\nExec=/stable/bin/taos-appgroups run antigravity %U\nIcon=%1/antigravity-nobg.png\n"_s.arg(iconsDir());
        writeFile(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s, old.toUtf8());
        const QString hand = iconsDir() + u"/kilo-desktop.png"_s, nobg = iconsDir() + u"/antigravity-nobg.png"_s;
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), hand);                       // install did not replace it
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(readAll(iconLine(u"antigravity"_s)), QByteArray("\x89PNG-nobg"));   // the launcher's own icon was adopted
        QCOMPARE(cli({u"move"_s, u"kilo-desktop"_s, u"--to"_s, u"group:dev-apps"_s}, kiloEnv()).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), hand);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 0);
        QCOMPARE(readAll(iconLine(u"antigravity"_s)), QByteArray("\x89PNG-nobg"));
        QCOMPARE(cli({u"launchers"_s, u"sync"_s}).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), hand);
        QCOMPARE(readAll(iconLine(u"antigravity"_s)), QByteArray("\x89PNG-nobg"));
        const QString newSha = u"aa"_s.repeated(32);
        const Run u = cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.1.0#"_s + newSha, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}, {u"FAKE_SHA"_s, newSha}});
        QVERIFY2(u.code == 0, qPrintable(u.raw + u.err));
        QCOMPARE(iconLine(u"kilo-desktop"_s), hand);                       // and the bytes are untouched
        QCOMPARE(readAll(hand), QByteArray("\x89PNG-by-hand"));
        QCOMPARE(readAll(nobg), QByteArray("\x89PNG-nobg"));
        // removing the app keeps what a person made
        QCOMPARE(cli({u"remove"_s, u"kilo-desktop"_s}).code, 0);
        QVERIFY(QFileInfo::exists(hand));
    }
    void anOverrideAlwaysWinsAndCanBeCleared()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        const QString mine = m_tmp.path() + u"/mine.png"_s;
        writeFile(mine, "\x89PNG-mine");
        QCOMPARE(cli({u"icon"_s, u"set"_s, u"kilo-desktop"_s, mine}).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), iconsDir() + u"/kilo-desktop.override.png"_s);
        QCOMPARE(cli({u"move"_s, u"kilo-desktop"_s, u"--to"_s, u"group:dev-apps"_s}, kiloEnv()).code, 0);
        QCOMPARE(readAll(iconLine(u"kilo-desktop"_s)), QByteArray("\x89PNG-mine"));
        QCOMPARE(cli({u"icon"_s, u"set"_s, u"kilo-desktop"_s, m_tmp.path() + u"/missing.png"_s}).code, 2);
        QCOMPARE(cli({u"icon"_s, u"clear"_s, u"kilo-desktop"_s}).code, 0);
        QCOMPARE(iconLine(u"kilo-desktop"_s), iconsDir() + u"/kilo-desktop.pkg.png"_s);
        const Run l = cli({u"list"_s, u"--json"_s});
        QCOMPARE(l.last.value(u"apps"_s).toArray()[0].toObject().value(u"icon_path"_s).toString(), iconsDir() + u"/kilo-desktop.pkg.png"_s);
    }

    // ---------------------------------------------------------------- two launches at once

    QMap<QString, QString> sessionEnv()
    {
        const QString rt = m_tmp.path() + u"/rt"_s;
        QDir().mkpath(rt);
        writeFile(rt + u"/wayland-test"_s, "x");
        return {{u"XDG_RUNTIME_DIR"_s, rt}, {u"WAYLAND_DISPLAY"_s, u"wayland-test"_s}};
    }
    void aDeviceEditThatLosesTheRaceIsRetried()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QMap<QString, QString> env = sessionEnv();
        env.insert(u"FAKE_ETAG_FAILS"_s, u"2"_s);
        QFile::remove(fakeDir + u"/etag.count"_s);
        const Run r = cli({u"run"_s, u"antigravity"_s}, env);
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QCOMPARE(readAll(fakeDir + u"/etag.count"_s).trimmed(), QByteArray("2"));
    }
    void twoLaunchesOfOneGroupDoNotEditItsDevicesAtTheSameTime()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(cli({u"install"_s, u"chromium"_s}).code, 0);
        QDir(fakeDir + u"/busy"_s).removeRecursively();
        QFile::remove(fakeDir + u"/conflicts"_s);
        QMap<QString, QString> env = sessionEnv();
        env.insert(u"FAKE_SERIALIZE_CHECK"_s, u"1"_s);
        QProcessEnvironment e = QProcessEnvironment::systemEnvironment();
        e.insert(u"TAOS_APPGROUPS_INCUS"_s, QStringLiteral(FAKE_INCUS));
        e.insert(u"FAKE_DIR"_s, fakeDir);
        e.insert(u"TAOS_APPGROUPS_DATA"_s, dataDir);
        e.insert(u"XDG_DATA_HOME"_s, xdgData);
        e.insert(u"HOME"_s, m_tmp.path() + u"/home"_s);
        e.insert(u"INCUS_SOCKET"_s, u"/nonexistent"_s);
        e.insert(u"TAOS_APPGROUPS_DRI_DIR"_s, m_tmp.path() + u"/dri"_s);
        e.insert(u"FAKE_OWNER"_s, QString::number(::getuid()));
        for (auto it = env.begin(); it != env.end(); ++it)
            e.insert(it.key(), it.value());
        QProcess a, b;
        a.setProcessEnvironment(e);
        b.setProcessEnvironment(e);
        a.start(QStringLiteral(APPGROUPS_CLI), {u"run"_s, u"antigravity"_s});
        b.start(QStringLiteral(APPGROUPS_CLI), {u"run"_s, u"chromium"_s});
        QVERIFY(a.waitForFinished(60000) && b.waitForFinished(60000));
        QVERIFY2(a.exitCode() == 0 && b.exitCode() == 0, qPrintable(QString::fromUtf8(a.readAllStandardError() + b.readAllStandardError())));
        QVERIFY2(!QFileInfo::exists(fakeDir + u"/conflicts"_s), "the two launches edited the group's devices at the same time");
    }

    void anAppInstalledBeforeTheLicenceSystemCanAcceptItLater()
    {
        // an Antigravity installed by an earlier engine: schema 1 manifest, licence a plain string, no acceptance
        QJsonObject man = QJsonDocument::fromJson(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s)).object();
        man[u"schema"_s] = 1;
        man[u"licence"_s] = u"proprietary-freeware"_s;
        QJsonObject rec{{u"group"_s, u"default"_s}, {u"target"_s, QJsonObject{{u"kind"_s, u"group"_s}, {u"id"_s, u"default"_s}}},
                        {u"installed_at"_s, u"2026-11-24T01:50:51Z"_s}, {u"manifest"_s, man}};
        writeFile(dataDir + u"/installed.json"_s, QJsonDocument(QJsonObject{{u"apps"_s, QJsonObject{{u"antigravity"_s, rec}}}}).toJson());
        QCOMPARE(indexRec(u"antigravity"_s).value(u"licence_accepted"_s).toObject().isEmpty(), true);
        const Run r = cli({u"licence"_s, u"accept"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s, u"--json"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QJsonObject acc = indexRec(u"antigravity"_s).value(u"licence_accepted"_s).toObject();
        QCOMPARE(acc.value(u"url"_s).toString(), u"https://policies.google.com/terms"_s);
        QCOMPARE(acc.value(u"accepted_by"_s).toString(), theAccount() + u" (confirmed in the Store)"_s);
        QCOMPARE(indexRec(u"antigravity"_s).value(u"installed_at"_s).toString(), u"2026-11-24T01:50:51Z"_s);   // nothing else changed
        QCOMPARE(cli({u"licence"_s, u"accept"_s, u"nosuchapp"_s}).code, 3);
        QCOMPARE(cli({u"licence"_s, u"accept"_s, u"chromium"_s}).code, 3);   // not installed
    }


    // ---------------------------------------------------------------- the person's own confirmation (review round)

    void aCommandLineFlagAloneCanNeverAcceptALicence()
    {
        // the old way: --accept-licence <id> --accepted-by <text>, no confirmation from the Store
        const Run r = cli({u"install"_s, u"claude-desktop"_s, u"--accept-licence"_s, u"claude-desktop"_s, u"--accepted-by"_s, u"the person"_s, u"--json"_s}, claudeEnv());
        QCOMPARE(r.code, 10);
        QCOMPARE(r.last.value(u"licence"_s).toObject().value(u"url"_s).toString(), u"https://www.anthropic.com/legal/consumer-terms"_s);
        QVERIFY(r.last.value(u"error"_s).toString().contains(u"Store"_s));
        QVERIFY(!QFileInfo::exists(dataDir + u"/installed.json"_s) || indexRec(u"claude-desktop"_s).isEmpty());
        QVERIFY2(!callsLog().contains(u"apt-get"_s), "nothing is installed");
        // a made-up nonce is no better
        const Run fake = cli({u"install"_s, u"claude-desktop"_s, u"--json"_s}, [&] { auto e = claudeEnv(); e.insert(u"TAOS_OWNER_NONCE"_s, u"00"_s.repeated(24)); return e; }());
        QCOMPARE(fake.code, 10);
        // and neither does `licence accept`
        QJsonObject man = QJsonDocument::fromJson(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s)).object();
        QJsonObject rec{{u"group"_s, u"default"_s}, {u"manifest"_s, man}};
        writeFile(dataDir + u"/installed.json"_s, QJsonDocument(QJsonObject{{u"apps"_s, QJsonObject{{u"antigravity"_s, rec}}}}).toJson());
        QCOMPARE(cli({u"licence"_s, u"accept"_s, u"antigravity"_s, u"--accepted-by"_s, u"the person"_s}).code, 10);
        QVERIFY(indexRec(u"antigravity"_s).value(u"licence_accepted"_s).toObject().isEmpty());
    }
    void aConfirmationIsSingleUseAndForThatAppOnly()
    {
        QMap<QString, QString> env = claudeEnv();
        const QString nonce = gate->mint(QLatin1String(kActionLicence), u"claude-desktop"_s);
        env.insert(u"TAOS_OWNER_NONCE"_s, nonce);
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s}, env).code, 0);
        // the same nonce again, for a fresh install: refused
        QCOMPARE(cli({u"remove"_s, u"claude-desktop"_s}).code, 0);
        const Run again = cli({u"install"_s, u"claude-desktop"_s, u"--json"_s}, env);
        QCOMPARE(again.code, 10);
        QVERIFY2(again.last.value(u"error"_s).toString().contains(u"already used"_s), qPrintable(again.raw));
        // a confirmation for another app, or for another action, is no use here
        QMap<QString, QString> other = claudeEnv();
        other.insert(u"TAOS_OWNER_NONCE"_s, gate->mint(QLatin1String(kActionLicence), u"kilo-desktop"_s));
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s}, other).code, 10);
        other.insert(u"TAOS_OWNER_NONCE"_s, gate->mint(QLatin1String(kActionUpdate), u"claude-desktop"_s));
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s}, other).code, 10);
    }
    void theStoreCanMintButTheSocketOffersNoWayToMint()
    {
        // the Store path works (everything else in this file goes through it); the socket only consumes
        const QString nonce = gate->mint(QLatin1String(kActionLicence), u"x"_s);
        QVERIFY(!nonce.isEmpty());
        QVERIFY(!consumeOwnerNonce(nonce, QLatin1String(kActionLicence), u"y"_s).ok);     // wrong subject, and now used up
        QVERIFY(!consumeOwnerNonce(nonce, QLatin1String(kActionLicence), u"x"_s).ok);
        const QString n2 = gate->mint(QLatin1String(kActionLicence), u"x"_s);
        const OwnerProof ok = consumeOwnerNonce(n2, QLatin1String(kActionLicence), u"x"_s);
        QVERIFY(ok.ok);
        QCOMPARE(ok.by, theAccount() + u" (confirmed in the Store)"_s);
        // raw socket: a "mint" request is refused
        const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        sockaddr_un a{};
        a.sun_family = AF_UNIX;
        const QByteArray sp = ownerSocketPath().toUtf8();
        ::memcpy(a.sun_path, sp.constData(), size_t(sp.size()));
        QCOMPARE(::connect(fd, reinterpret_cast<sockaddr *>(&a), sizeof a), 0);
        const QByteArray q = "{\"op\":\"mint\",\"action\":\"licence.accept\",\"subject\":\"x\"}\n";
        QVERIFY(::write(fd, q.constData(), size_t(q.size())) > 0);
        char buf[512] = {};
        const ssize_t n = ::read(fd, buf, sizeof buf - 1);
        ::close(fd);
        QVERIFY(n > 0 && QByteArray(buf).contains("\"ok\":false") && !QByteArray(buf).contains("nonce"));
        // the socket is private to the person
        QCOMPARE(QFileInfo(ownerSocketPath()).permissions() & (QFile::ReadGroup | QFile::WriteGroup | QFile::ReadOther | QFile::WriteOther), QFileDevice::Permissions());
    }

    // ---------------------------------------------------------------- keys, mime types, updates, callbacks, moves

    void aKeyFileWithAnExtraKeyOrOnlyASubkeyMatchIsRefused_data()
    {
        QTest::addColumn<QString>("knob");
        QTest::newRow("two primary keys") << u"FAKE_KEY_EXTRA"_s;
        QTest::newRow("fingerprint only on a subkey") << u"FAKE_KEY_SUBONLY"_s;
    }
    void aKeyFileWithAnExtraKeyOrOnlyASubkeyMatchIsRefused()
    {
        QFETCH(QString, knob);
        // claude-desktop pins 31DD...; the fake key listing carries it as the primary key unless a knob changes that
        QMap<QString, QString> env = claudeEnv();
        env.insert(knob, u"1"_s);
        const Run r = cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, env);
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"exactly one key"_s), qPrintable(r.err));
        QVERIFY2(!callsLog().contains(u"--dearmor"_s), "an untrusted key is never prepared for apt");
        // the clean listing (one primary key plus a decoy subkey) is accepted
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, claudeEnv()).code, 0);
    }
    void theStoredKeyIsCheckedAgainOnEveryUpdate()
    {
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, claudeEnv()).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QMap<QString, QString> env = claudeEnv();
        env.insert(u"FAKE_CANDIDATE"_s, u"2.0.0"_s);
        QCOMPARE(cli({u"update"_s, u"claude-desktop"_s, u"--check"_s}, env).code, 0);
        env.insert(u"FAKE_STORED_FPR"_s, u"1234567890123456789012345678901234567890"_s);   // the key on disk is not the pinned one any more
        const Run r = cli({u"update"_s, u"claude-desktop"_s, u"--json"_s}, env);
        QCOMPARE(r.code, 6);
        QVERIFY2(r.last.value(u"error"_s).toString().contains(u"not exactly the pinned key"_s), qPrintable(r.raw));
        QVERIFY(!callsLog().contains(u"--only-upgrade"_s));
    }
    void mimeTypesThatCouldInjectIntoALauncherAreRefused_data()
    {
        QTest::addColumn<QString>("mime");
        QTest::newRow("newline") << u"text/html\\nExec=/bin/false"_s;
        QTest::newRow("semicolon") << u"text/html;x-scheme-handler/evil"_s;
        QTest::newRow("equals") << u"text/html=1"_s;
        QTest::newRow("trailing newline") << u"x-scheme-handler/claude\\n"_s;
        QTest::newRow("upper scheme") << u"x-scheme-handler/Claude"_s;
        QTest::newRow("no subtype") << u"text/"_s;
        QTest::newRow("unknown type") << u"evil/thing"_s;
    }
    void mimeTypesThatCouldInjectIntoALauncherAreRefused()
    {
        QFETCH(QString, mime);
        QString text = QString::fromUtf8(readAll(QStringLiteral(CATALOG_DIR) + u"/claude-desktop.json"_s));
        QVERIFY(text.contains(u"x-scheme-handler/claude"_s));
        text.replace(u"x-scheme-handler/claude"_s, mime);
        Manifest m;
        QString err;
        QVERIFY2(!parseManifest(text.toUtf8(), &m, &err), qPrintable(mime));
        QVERIFY2(err.contains(u"mime_types"_s), qPrintable(err));
        // through the real caller: a catalogue entry carrying it is refused at install, and no launcher is written
        QDir().mkpath(catDir);
        writeFile(catDir + u"/claude-desktop.json"_s, text.toUtf8());
        const Run r = cli({u"install"_s, u"claude-desktop"_s, u"--test-owner-ok"_s, u"claude-desktop"_s}, [&] { auto e = claudeEnv(); e.insert(u"TAOS_APPGROUPS_CATALOG"_s, catDir); return e; }());
        QCOMPARE(r.code, 5);
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-claude-desktop.desktop"_s));
        QFile::remove(catDir + u"/claude-desktop.json"_s);
    }
    void aNewerVersionFromTheFeedAloneNeedsThePersonAndIsRecorded()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        const QString newSha = u"aa"_s.repeated(32);
        QMap<QString, QString> env{{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}, {u"FAKE_SHA"_s, newSha}, {u"FAKE_VERSION"_s, u"1.1.0"_s}};
        // --check shows exactly what was and was not verified
        const Run chk = cli({u"update"_s, u"kilo-desktop"_s, u"--check"_s, u"--json"_s}, env);
        const QJsonObject v = chk.last.value(u"verification"_s).toObject();
        QCOMPARE(v.value(u"checksum_pinned_in_catalogue"_s).toBool(), false);
        QCOMPARE(v.value(u"signed_source"_s).toBool(), false);
        QVERIFY(v.value(u"summary"_s).toString().startsWith(u"NOT verified"_s));
        QFile::remove(fakeDir + u"/calls.log"_s);
        // no silent update: without the person's confirmation nothing is downloaded or installed
        const Run bare = cli({u"update"_s, u"kilo-desktop"_s, u"--json"_s}, env);
        QCOMPARE(bare.code, 10);
        QCOMPARE(bare.last.value(u"confirm"_s).toObject().value(u"subject"_s).toString(), u"kilo-desktop@1.1.0#"_s + newSha);
        QVERIFY(!callsLog().contains(u"curl -fL"_s));
        // a confirmation for a different checksum is no use
        QCOMPARE(cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.1.0#"_s + u"bb"_s.repeated(32)}, env).code, 10);
        // the person confirms: installed, and the history says what was and was not verified and who confirmed
        const Run ok = cli({u"update"_s, u"kilo-desktop"_s, u"--test-owner-update"_s, u"kilo-desktop@1.1.0#"_s + newSha, u"--json"_s}, env);
        QVERIFY2(ok.code == 0, qPrintable(ok.raw + ok.err));
        const QJsonArray hist = indexRec(u"kilo-desktop"_s).value(u"update_history"_s).toArray();
        QCOMPARE(hist.size(), 1);
        QCOMPARE(hist[0].toObject().value(u"confirmed_by"_s).toString(), theAccount() + u" (confirmed in the Store)"_s);
        QCOMPARE(hist[0].toObject().value(u"verification"_s).toObject().value(u"checksum_pinned_in_catalogue"_s).toBool(), false);
    }
    void theVersionTheCatalogueCoversIsCheckedAgainstItsPinnedChecksum()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--test-owner-ok"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        // the feed claims the pinned version but with another checksum: refused outright, even before any confirmation
        const Run r = cli({u"update"_s, u"kilo-desktop"_s, u"--check"_s, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.0.0"_s, u"cc"_s.repeated(32))}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.last.value(u"error"_s).toString().contains(u"pinned in the catalogue"_s), qPrintable(r.raw));
    }
    void onlyTheRedirectPathIsForwardedAsTheCallback()
    {
        QVERIFY(callbackRequestMatches("GET /oauth-callback?code=a&state=b HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n", u"/oauth-callback"_s));
        QVERIFY(!callbackRequestMatches("GET /other HTTP/1.1\r\n\r\n", u"/oauth-callback"_s));
        QVERIFY(!callbackRequestMatches("POST /oauth-callback HTTP/1.1\r\n\r\n", u"/oauth-callback"_s));
        QVERIFY(!callbackRequestMatches("GET /oauth-callback/../x HTTP/1.1\r\n\r\n", u"/oauth-callback"_s));
        QVERIFY(!callbackRequestMatches("GET //evil/oauth-callback HTTP/1.1\r\n\r\n", u"/oauth-callback"_s));
        QVERIFY(!callbackRequestMatches("\x16\x03\x01 not http at all", u"/oauth-callback"_s));
        // real sockets: a stray connection and a wrong path are answered 404 and do not count; the callback is relayed once
        const QString up = m_tmp.path() + u"/up.sock"_s;
        const int ufd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        sockaddr_un ua{};
        ua.sun_family = AF_UNIX;
        ::memcpy(ua.sun_path, up.toUtf8().constData(), size_t(up.toUtf8().size()));
        QCOMPARE(::bind(ufd, reinterpret_cast<sockaddr *>(&ua), sizeof ua), 0);
        QCOMPARE(::listen(ufd, 2), 0);
        QString err;
        const int port = 51901;
        const int lfd = listenCallbackPort(port, &err);
        QVERIFY2(lfd >= 0, qPrintable(err));
        QString again;
        QVERIFY(listenCallbackPort(port, &again) < 0);   // a port somebody holds is refused
        bool relayed = false;
        std::thread server([&] { relayed = serveCallback(lfd, u"/oauth-callback"_s, up, 10); });
        std::thread app([&] {
            const int c = ::accept(ufd, nullptr, nullptr);
            char b[1024] = {};
            const ssize_t n = ::read(c, b, sizeof b - 1);
            if (n > 0 && QByteArray(b).contains("/oauth-callback"))
                if (::write(c, "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", 40) < 0) {}
            ::close(c);
        });
        auto fetch = [&](const QByteArray &req) {
            const int s = ::socket(AF_INET, SOCK_STREAM, 0);
            sockaddr_in a{};
            a.sin_family = AF_INET;
            a.sin_port = htons(port);
            a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            QByteArray got;
            if (::connect(s, reinterpret_cast<sockaddr *>(&a), sizeof a) == 0) {
                if (::write(s, req.constData(), size_t(req.size())) < 0) {}
                char b[1024];
                ssize_t n;
                while ((n = ::read(s, b, sizeof b)) > 0)
                    got.append(b, int(n));
            }
            ::close(s);
            return got;
        };
        QVERIFY(fetch("GET /favicon.ico HTTP/1.1\r\n\r\n").startsWith("HTTP/1.1 404"));
        { const int s = ::socket(AF_INET, SOCK_STREAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(port); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
          QCOMPARE(::connect(s, reinterpret_cast<sockaddr *>(&a), sizeof a), 0); ::close(s); }   // connects and sends nothing: not the callback
        QVERIFY(fetch("GET /oauth-callback?code=1 HTTP/1.1\r\n\r\n").contains("200 OK"));
        server.join();
        app.join();
        QVERIFY(relayed);
        ::close(lfd);
        ::close(ufd);
    }
    void aMovedProfileDropsPerHostLocksAndSockets()
    {
        // the real cleanup script on a real directory shaped like a Chromium + Firefox profile
        QTemporaryDir t;
        const QString home = t.path();
        QDir().mkpath(home + u"/.config/chromium/Default"_s);
        QDir().mkpath(home + u"/.mozilla/firefox/p.default"_s);
        writeFile(home + u"/.config/chromium/Default/Preferences"_s, "{}");
        writeFile(home + u"/.mozilla/firefox/p.default/prefs.js"_s, "x");
        writeFile(home + u"/.mozilla/firefox/p.default/.parentlock"_s, "");
        for (const char *n : {"SingletonLock", "SingletonCookie", "SingletonSocket"})
            QVERIFY(QFile::link(u"user-apps-123"_s, home + u"/.config/chromium/"_s + QLatin1String(n)));
        QVERIFY(QFile::link(u"10.0.0.1:+1234"_s, home + u"/.mozilla/firefox/p.default/lock"_s));
        const int sfd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        sockaddr_un ua{};
        ua.sun_family = AF_UNIX;
        const QByteArray sp = (home + u"/.config/chromium/ipc.sock"_s).toUtf8();
        ::memcpy(ua.sun_path, sp.constData(), size_t(sp.size()));
        QCOMPARE(::bind(sfd, reinterpret_cast<sockaddr *>(&ua), sizeof ua), 0);
        QProcess p;
        p.start(u"sh"_s, {u"-c"_s, hostLockCleanupScript(), u"sh"_s, home, u".config"_s, u".mozilla"_s});
        QVERIFY(p.waitForFinished(20000) && p.exitCode() == 0);
        ::close(sfd);
        for (const QString &gone : {u"/.config/chromium/SingletonLock"_s, u"/.config/chromium/SingletonCookie"_s, u"/.config/chromium/SingletonSocket"_s,
                                    u"/.config/chromium/ipc.sock"_s, u"/.mozilla/firefox/p.default/.parentlock"_s, u"/.mozilla/firefox/p.default/lock"_s})
            QVERIFY2(!QFileInfo(home + gone).isSymLink() && !QFileInfo::exists(home + gone), qPrintable(gone));
        QVERIFY(QFileInfo::exists(home + u"/.config/chromium/Default/Preferences"_s));   // the profile itself stays
        QVERIFY(QFileInfo::exists(home + u"/.mozilla/firefox/p.default/prefs.js"_s));
        // through the real caller: a move runs it on the copy before comparing, and the comparison ignores those files
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 0);
        const QString c = callsLog();
        QVERIFY(c.contains(u"host-locks"_s) && c.indexOf(u"host-locks"_s) < c.indexOf(u"content-sum"_s));
        QVERIFY(c.contains(u"! -name SingletonLock"_s));
    }
    void theGtkDarkSettingIsOwnedAndKeptInStepOnEveryLaunch()
    {
        // the real script: a hand-set value is replaced, other keys stay, a missing file is made
        QTemporaryDir t;
        const QString f3 = t.path() + u"/.config/gtk-3.0/settings.ini"_s;
        writeFile(f3, "[Settings]\ngtk-application-prefer-dark-theme=1\ngtk-font-name=Sans 10\n");
        QProcess p;
        p.start(u"sh"_s, {u"-c"_s, gtkDarkSettingScript(), u"sh"_s, t.path(), u"0"_s});
        QVERIFY(p.waitForFinished(20000) && p.exitCode() == 0);
        QCOMPARE(QString::fromUtf8(readAll(f3)), u"[Settings]\ngtk-application-prefer-dark-theme=0\ngtk-font-name=Sans 10\n"_s);
        QCOMPARE(QString::fromUtf8(readAll(t.path() + u"/.config/gtk-4.0/settings.ini"_s)), u"[Settings]\ngtk-application-prefer-dark-theme=0\n"_s);
        p.start(u"sh"_s, {u"-c"_s, gtkDarkSettingScript(), u"sh"_s, t.path(), u"1"_s});
        QVERIFY(p.waitForFinished(20000) && p.exitCode() == 0);
        QVERIFY(QString::fromUtf8(readAll(f3)).contains(u"prefer-dark-theme=1\n"_s));
        QCOMPARE(QString::fromUtf8(readAll(t.path() + u"/.config/gtk-4.0/settings.ini"_s)).count(u"prefer-dark"_s), 1);
    }
    void aMoveWhoseCopyHasTheSameSizeButOtherContentIsRefused()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        const Run r = cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s, u"--json"_s}, {{u"FAKE_CONTENT_MISMATCH"_s, u"1"_s}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.last.value(u"error"_s).toString().contains(u"does not match"_s), qPrintable(r.raw));
        QCOMPARE(indexRec(u"antigravity"_s).value(u"group"_s).toString(), u"default"_s);
        QVERIFY2(callsLog().contains(u"content-sum"_s), "the copy is compared by content, not only by size");
    }
    void aLaunchIsRefusedWhileAMoveOfThatAppIsBetweenItsChecksAndTheSwitch()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        // another command holds the app exclusively, as a move does from before its running check until the switch
        QDir().mkpath(dataDir + u"/locks"_s);
        const int fd = ::open(QFile::encodeName(dataDir + u"/locks/app-antigravity.lock"_s).constData(), O_RDWR | O_CREAT, 0600);
        QCOMPARE(::flock(fd, LOCK_EX | LOCK_NB), 0);
        QMap<QString, QString> env = launcherEnv();
        QCOMPARE(cli({u"run"_s, u"antigravity"_s}, env).code, 7);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 7);
        ::close(fd);
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 0);
    }

    // ---------------------------------------------------------------- appearance

    void anAppStartsInTheModeTheDesktopIsIn()
    {
        QCOMPARE(cli({u"install"_s, u"chromium"_s}).code, 0);
        QMap<QString, QString> env = sessionEnv();
        const QString cfg = m_tmp.path() + u"/cfg"_s;
        env.insert(u"XDG_CONFIG_HOME"_s, cfg);
        writeFile(cfg + u"/taos/appearance.json"_s, "{\"mode\": \"dark\", \"theme\": \"graphite\"}");
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"run"_s, u"chromium"_s}, env).code, 0);
        QVERIFY2(callsLog().contains(u"--env GTK_THEME=Adwaita:dark"_s), qPrintable(callsLog()));
        QVERIFY2(callsLog().contains(u"gtk-dark"_s) && callsLog().contains(u"/home/taos/apps/"_s) , qPrintable(callsLog()));
        writeFile(cfg + u"/taos/appearance.json"_s, "{\"mode\": \"light\", \"theme\": \"graphite\"}");
        QFile::remove(fakeDir + u"/calls.log"_s);
        QCOMPARE(cli({u"run"_s, u"chromium"_s}, env).code, 0);
        QVERIFY2(callsLog().contains(u"--env GTK_THEME=Adwaita"_s) && !callsLog().contains(u"Adwaita:dark"_s), qPrintable(callsLog()));
        QFile::remove(fakeDir + u"/calls.log"_s);
        QFile::remove(cfg + u"/taos/appearance.json"_s);
        QCOMPARE(cli({u"run"_s, u"chromium"_s}, env).code, 0);   // no appearance file: nothing is forced
        QVERIFY(!callsLog().contains(u"GTK_THEME"_s));
    }

    // ---------------------------------------------------------------- links: the URL broker

    QString launchLog() { return QString::fromUtf8(readAll(fakeDir + u"/launch.log"_s)); }
    QMap<QString, QString> launcherEnv()
    {
        // the "taos-appgroups" the broker starts for `run` is a recorder, so what it would have run can be read back
        const QString rec = m_tmp.path() + u"/recorder.sh"_s;
        writeFile(rec, "#!/bin/sh\necho \"$*\" >> \"$FAKE_DIR/launch.log\"\n");
        QFile::setPermissions(rec, QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
        return {{u"TAOS_APPGROUPS_EXEC"_s, rec}};
    }
    void setUpBrokerApps()
    {
        QCOMPARE(cli({u"install"_s, u"chromium"_s}).code, 0);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QFile::remove(fakeDir + u"/launch.log"_s);
        QFile::remove(fakeDir + u"/calls.log"_s);
    }
    void linksGoToTheChosenBrowserAndOnlyWebLinksDo()
    {
        setUpBrokerApps();
        const QMap<QString, QString> env = launcherEnv();
        QCOMPARE(cli({u"open-url"_s, u"https://example.org/a"_s}, env).code, 9);   // no browser chosen yet: said so, nothing guessed
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"nosuchapp"_s}).code, 3);
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"antigravity"_s}).code, 2);          // not a browser (no web handler)
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"chromium"_s}).code, 0);
        QCOMPARE(cli({u"browser"_s, u"get"_s, u"--json"_s}).last.value(u"browser"_s).toString(), u"chromium"_s);
        const Run r = cli({u"open-url"_s, u"--from"_s, u"default"_s, u"https://example.org/a?b=1"_s, u"--json"_s}, env);
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QTRY_VERIFY2(launchLog().contains(u"run chromium https://example.org/a?b=1"_s), qPrintable(launchLog()));
        QFile::remove(fakeDir + u"/launch.log"_s);
        // nothing but web links, and the schemes of installed apps, is ever opened
        for (const QString &bad : {u"file:///etc/passwd"_s, u"javascript:alert(1)"_s, u"data:text/html,x"_s, u"ssh://host/"_s, u"https://"_s, u"not a url"_s,
                                   u"https://example.org/\nx"_s})
            QVERIFY2(cli({u"open-url"_s, bad}, env).code == 5, qPrintable(bad));
        QTest::qWait(300);
        QVERIFY2(launchLog().isEmpty(), qPrintable(launchLog()));
    }
    void aSignInCallbackSchemeGoesBackToTheAppThatOwnsIt()
    {
        setUpBrokerApps();
        const QMap<QString, QString> env = launcherEnv();
        QCOMPARE(cli({u"open-url"_s, u"antigravity://auth/callback?code=abc"_s}, env).code, 0);
        QTRY_VERIFY2(launchLog().contains(u"run antigravity antigravity://auth/callback?code=abc"_s), qPrintable(launchLog()));
        QCOMPARE(cli({u"open-url"_s, u"claude://claude.ai/new"_s}, env).code, 5);   // nothing installed registers it
    }
    void aGroupCanHaveItsOwnBrowser()
    {
        setUpBrokerApps();
        const QMap<QString, QString> env = launcherEnv();
        QCOMPARE(cli({u"install"_s, u"firefox"_s, u"--target"_s, u"group:dev-apps"_s}).code, 0);
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"chromium"_s}).code, 0);
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"firefox"_s, u"--group"_s, u"dev-apps"_s}).code, 0);
        QCOMPARE(cli({u"open-url"_s, u"--from"_s, u"dev-apps"_s, u"https://work.example/"_s}, env).code, 0);
        QCOMPARE(cli({u"open-url"_s, u"--from"_s, u"default"_s, u"https://home.example/"_s}, env).code, 0);
        QTRY_VERIFY(launchLog().contains(u"run firefox https://work.example/"_s) && launchLog().contains(u"run chromium https://home.example/"_s));
        QCOMPARE(cli({u"browser"_s, u"clear"_s, u"--group"_s, u"dev-apps"_s}).code, 0);
        QFile::remove(fakeDir + u"/launch.log"_s);
        QCOMPARE(cli({u"open-url"_s, u"--from"_s, u"dev-apps"_s, u"https://work.example/"_s}, env).code, 0);
        QTRY_VERIFY(launchLog().contains(u"run chromium https://work.example/"_s));
    }
    void aLocalhostCallbackIsForwardedFromTheBrowsersGroupToTheAppsGroupForThatPortOnly()
    {
        setUpBrokerApps();   // chromium is in default; antigravity is moved to dev-apps and asks for a localhost redirect
        QCOMPARE(cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s}).code, 0);
        QCOMPARE(cli({u"browser"_s, u"set"_s, u"chromium"_s}).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        QMap<QString, QString> env = launcherEnv();
        env.insert(u"TAOS_APPGROUPS_FORWARD_SECONDS"_s, u"1"_s);
        const QString url = u"https://accounts.google.com/o/oauth2/v2/auth?client_id=x&redirect_uri=http%3A%2F%2F127.0.0.1%3A51234%2Foauth-callback&scope=a"_s;
        const Run r = cli({u"open-url"_s, u"--from"_s, u"dev-apps"_s, url, u"--json"_s}, env);
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QCOMPARE(r.last.value(u"forwarded_port"_s).toInt(), 51234);
        const QString c = callsLog();
        // the app's group is reached over a unix socket only this uid can open (not a host TCP port anyone could use);
        // the browser's group listens on its own loopback and reaches this command's own checking listener on the host
        QVERIFY2(QRegularExpression(u"config device add appgroup-dev-apps fwd-51234 proxy listen=unix:\\S+\\.sock connect=tcp:127\\.0\\.0\\.1:51234 bind=host uid=%1 gid=\\d+ mode=0600\n"_s.arg(::getuid())).match(c).hasMatch(), qPrintable(c));
        QVERIFY(!c.contains(u"listen=tcp:127.0.0.1:51234 connect=tcp:127.0.0.1:51234 bind=host"_s));
        QVERIFY2(c.contains(u"config device add user-apps fwd-51234 proxy listen=tcp:127.0.0.1:51234 connect=tcp:127.0.0.1:51234 bind=container"_s), qPrintable(c));
        QVERIFY(!c.contains(u"fwd-80"_s) && !c.contains(u"0.0.0.0"_s));
        // closed again after the timeout
        QTRY_VERIFY2(callsLog().contains(u"config device remove appgroup-dev-apps fwd-51234"_s) && callsLog().contains(u"config device remove user-apps fwd-51234"_s), qPrintable(callsLog()));
    }
    void usageAndRunWithoutSession()
    {
        QCOMPARE(cli({u"bogus"_s}).code, 2);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--test-owner-ok"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(cli({u"run"_s, u"antigravity"_s}, {{u"XDG_RUNTIME_DIR"_s, u"/nonexistent"_s}}).code, 8);
        QCOMPARE(cli({u"run"_s, u"notinstalled"_s}).code, 3);
    }
};

QTEST_MAIN(TstAppGroups)
#include "tst_appgroups.moc"
