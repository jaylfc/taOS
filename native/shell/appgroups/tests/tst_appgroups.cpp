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
        QCOMPARE(g.size(), 1);
        QCOMPARE(g[0].toObject().value(u"container"_s).toString(), u"user-apps"_s);
        QCOMPARE(g[0].toObject().value(u"display_name"_s).toString(), u"My apps"_s);
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
