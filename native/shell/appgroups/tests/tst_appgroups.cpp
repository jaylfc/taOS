// SPDX-License-Identifier: AGPL-3.0-or-later
// The container apps engine: manifest rules, the CLI with a fake incus (install, refuse, remove keeps data, listing
// shows only your groups), launcher entry, exit codes. No real container is touched.
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QTemporaryDir>
#include <QtTest>

#include <unistd.h>

#include "manifest.h"

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
    Run cli(const QStringList &args, const QMap<QString, QString> &env = {})
    {
        QProcess p;
        QProcessEnvironment e = QProcessEnvironment::systemEnvironment();
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
        QDir().mkpath(m_tmp.path() + u"/home"_s);
        // a GPU with two render nodes and a card node: the lowest render node is granted, never the card
        QDir().mkpath(m_tmp.path() + u"/dri"_s);
        for (const char *n : {"card0", "renderD129", "renderD130"})
            writeFile(m_tmp.path() + u"/dri/"_s + QLatin1String(n), "x");
    }
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
        const Run r = cli({u"install"_s, u"antigravity"_s, u"--json"_s});
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
        const Run again = cli({u"install"_s, u"antigravity"_s});
        QCOMPARE(again.code, 7);
    }
    void wrongChecksumIsRefusedAndLeavesNothing()
    {
        const Run r = cli({u"install"_s, u"antigravity"_s}, {{u"FAKE_SHA"_s, u"00"_s}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"pinned checksum"_s), qPrintable(r.err));
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QCOMPARE(cli({u"list"_s, u"--json"_s}).last.value(u"apps"_s).toArray().size(), 0);
    }
    void noRenderNodeSkipsTheGpuGrantWithAMessage()
    {
        const QString empty = m_tmp.path() + u"/nodri"_s;
        QDir().mkpath(empty);
        const Run r = cli({u"install"_s, u"antigravity"_s}, {{u"TAOS_APPGROUPS_DRI_DIR"_s, empty}});
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
        const Run r = cli({u"install"_s, u"nl"_s}, {{u"TAOS_APPGROUPS_CATALOG"_s, catDir}});
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
        const Run r = cli({u"install"_s, u"keyed"_s}, {{u"TAOS_APPGROUPS_CATALOG"_s, catDir}});
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
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        const Run h = cli({u"install"_s, u"antigravity"_s, u"--target"_s, u"host"_s, u"--accept-host-warning"_s, u"--json"_s});
        QCOMPARE(h.code, 9);
        QVERIFY(h.last.value(u"error"_s).toString().contains(u"not available yet"_s));
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--target"_s, u"worker:desk2"_s}).code, 9);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--target"_s, u"bogus"_s}).code, 2);
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-antigravity.desktop"_s));
        QVERIFY(!QString::fromUtf8(readAll(fakeDir + u"/calls.log"_s)).contains(u"exec"_s));
        QCOMPARE(cli({u"install"_s, u"antigravity"_s, u"--target"_s, u"group:default"_s}).code, 0);
    }

    // ---------------------------------------------------------------- schema 2, licence, new manifests

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
        QVERIFY(parseManifest(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s), &m, &err));
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
        const Run ok = cli({u"install"_s, u"claude-desktop"_s, u"--accept-licence"_s, u"claude-desktop"_s, u"--accepted-by"_s, u"jay (in session)"_s, u"--json"_s}, claudeEnv());
        QVERIFY2(ok.code == 0, qPrintable(ok.raw + ok.err));
        const QJsonObject acc = indexRec(u"claude-desktop"_s).value(u"licence_accepted"_s).toObject();
        QCOMPARE(acc.value(u"url"_s).toString(), u"https://www.anthropic.com/legal/consumer-terms"_s);
        QCOMPARE(acc.value(u"name"_s).toString(), u"Anthropic Consumer Terms of Service"_s);
        QCOMPARE(acc.value(u"accepted_by"_s).toString(), u"jay (in session)"_s);
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
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}).code, 6);   // default FAKE_SHA is not Kilo's
        QVERIFY(!QFileInfo::exists(xdgData + u"/applications/taos-appgroups-kilo-desktop.desktop"_s));
        const Run r = cli({u"install"_s, u"kilo-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}, kiloEnv());
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY(callsLog().contains(u"apt-get install -y /var/tmp/taos-dl-kilo-desktop.deb"_s));
    }
    void removingADebAppRemovesItsPackage()
    {
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--accept-licence"_s, u"claude-desktop"_s}, env).code, 0);
        QFile::remove(fakeDir + u"/calls.log"_s);
        const Run r = cli({u"move"_s, u"claude-desktop"_s, u"--to"_s, u"group:dev-apps"_s}, env);
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QString c = callsLog();
        QVERIFY2(c.contains(u"-cf - --numeric-owner .config/Claude .claude .cache/claude-desktop"_s), qPrintable(c));
        QVERIFY(!indexRec(u"claude-desktop"_s).value(u"licence_accepted"_s).toObject().isEmpty());   // acceptance travels with the app
    }
    void moveRefusesARunningApp()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QJsonObject man = QJsonDocument::fromJson(readAll(QStringLiteral(CATALOG_DIR) + u"/antigravity.json"_s)).object();
        QJsonObject rec{{u"group"_s, u"default"_s}, {u"target"_s, QJsonObject{{u"kind"_s, u"group"_s}, {u"id"_s, u"default"_s}}},
                        {u"installed_at"_s, u"2026-11-20T10:00:00Z"_s}, {u"manifest"_s, man}};
        writeFile(dataDir + u"/installed.json"_s, QJsonDocument(QJsonObject{{u"apps"_s, QJsonObject{{u"antigravity"_s, rec}}}}).toJson());
        QCOMPARE(cli({u"list"_s, u"--json"_s}).last.value(u"apps"_s).toArray().size(), 1);
        QCOMPARE(cli({u"launchers"_s, u"sync"_s}).code, 0);
        const Run r = cli({u"move"_s, u"antigravity"_s, u"--to"_s, u"group:dev-apps"_s});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QCOMPARE(indexRec(u"antigravity"_s).value(u"group"_s).toString(), u"dev-apps"_s);
        QCOMPARE(indexRec(u"antigravity"_s).value(u"installed_at"_s).toString(), u"2026-11-20T10:00:00Z"_s);
        const Run b = cli({u"backups"_s, u"--json"_s});
        QCOMPARE(b.last.value(u"backups"_s).toArray().size(), 1);
    }
    void backupsAreListedAndDeletedOnlyOnRequest()
    {
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
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
        QCOMPARE(cli({u"install"_s, u"kilo-desktop"_s, u"--accept-licence"_s, u"kilo-desktop"_s}, kiloEnv()).code, 0);
        const QString newSha = u"aa"_s.repeated(32);
        QFile::remove(fakeDir + u"/calls.log"_s);
        // the download's checksum is not the feed's: refused, nothing installed
        Run r = cli({u"update"_s, u"kilo-desktop"_s}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}});
        QCOMPARE(r.code, 6);
        QVERIFY2(r.err.contains(u"does not match"_s), qPrintable(r.err));
        QVERIFY(!callsLog().contains(u"apt-get install -y /var/tmp"_s));
        // matching checksum and a stopped app: installed, version recorded
        QFile::remove(fakeDir + u"/calls.log"_s);
        r = cli({u"update"_s, u"kilo-desktop"_s, u"--json"_s}, {{u"FAKE_FEED"_s, feed(u"1.1.0"_s, newSha)}, {u"FAKE_SHA"_s, newSha}, {u"FAKE_VERSION"_s, u"1.1.0"_s}});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        QVERIFY(callsLog().contains(u"kilo-desktop_1.1.0_amd64.deb"_s));
        QCOMPARE(indexRec(u"kilo-desktop"_s).value(u"installed_version"_s).toString(), u"1.1.0"_s);
        // a running app is never replaced
        r = cli({u"update"_s, u"kilo-desktop"_s}, {{u"FAKE_FEED"_s, feed(u"1.2.0"_s, u"bb"_s.repeated(32))}, {u"FAKE_RUNNING"_s, u"1"_s}});
        QCOMPARE(r.code, 7);
    }
    void updateOfAnAptAppUpgradesOnlyItsPackages()
    {
        QCOMPARE(cli({u"install"_s, u"claude-desktop"_s, u"--accept-licence"_s, u"claude-desktop"_s}, claudeEnv()).code, 0);
        Run r = cli({u"update"_s, u"claude-desktop"_s, u"--check"_s, u"--json"_s}, {{u"FAKE_CANDIDATE"_s, u"2.0.0"_s}});
        QCOMPARE(r.last.value(u"update_available"_s).toBool(), true);
        QCOMPARE(r.last.value(u"latest_version"_s).toString(), u"2.0.0"_s);
        QFile::remove(fakeDir + u"/calls.log"_s);
        r = cli({u"update"_s, u"claude-desktop"_s, u"--json"_s}, {{u"FAKE_CANDIDATE"_s, u"2.0.0"_s}, {u"FAKE_VERSION_AFTER"_s, u"2.0.0"_s}});
        QVERIFY2(r.code == 0, qPrintable(r.raw + r.err));
        const QString c = callsLog();
        QVERIFY(c.contains(u"apt-get install -y --no-install-recommends --only-upgrade claude-desktop"_s));
        QVERIFY(!c.contains(u"dist-upgrade"_s) && !c.contains(u"apt-get upgrade"_s));
        QCOMPARE(indexRec(u"claude-desktop"_s).value(u"installed_version"_s).toString(), u"2.0.0"_s);
        QCOMPARE(cli({u"update"_s, u"claude-desktop"_s}, {{u"FAKE_RUNNING"_s, u"1"_s}}).code, 7);
    }
    void usageAndRunWithoutSession()
    {
        QCOMPARE(cli({u"bogus"_s}).code, 2);
        QCOMPARE(cli({u"install"_s, u"antigravity"_s}).code, 0);
        QCOMPARE(cli({u"run"_s, u"antigravity"_s}, {{u"XDG_RUNTIME_DIR"_s, u"/nonexistent"_s}}).code, 8);
        QCOMPARE(cli({u"run"_s, u"notinstalled"_s}).code, 3);
    }
};

QTEST_MAIN(TstAppGroups)
#include "tst_appgroups.moc"
