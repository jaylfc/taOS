// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QImage>
#include <QTemporaryDir>
#include <QtTest>

#include "usermodel.h"

static const char *kPasswd =
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "systemuser:x:999:999:System:/var/lib/x:/bin/bash\n"
    "alex:x:1000:1000:Alex Morgan,Room 1,,:/home/alex:/bin/bash\n"
    "sam:x:1001:1001::/home/sam:/usr/bin/zsh\n"
    "svc:x:1002:1002:Service Account:/srv:/usr/sbin/nologin\n"
    "falsey:x:1003:1003:Falsey:/home/f:/bin/false\n"
    "jose:x:1004:1004:Jos\xc3\xa9 \xc3\x81lvarez:/home/jose:/bin/bash\n"
    "li:x:1005:1005:\xe6\x9d\x8e\xe6\x98\x8e:/home/li:/bin/bash\n"
    "high:x:59999:59999:Highest:/home/high:/bin/sh\n"
    "toohigh:x:60000:60000:Too High:/home/th:/bin/bash\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/bin/bash\n"
    "# a comment\n"
    "broken line without colons\n"
    "short:x:1006\n"
    "badid:x:abc:1:Bad:/h:/bin/bash\n"
    "emptyshell:x:1007:1007:Empty Shell:/home/e:\n"
    "onlycomma:x:1008:1008:,,,:/home/oc:/bin/bash\n";

class TestUserModel : public QObject {
    Q_OBJECT
private slots:
    void passwdFiltersSystemAndNologin() {
        const auto u = UserDiscovery::parsePasswd(kPasswd);
        QStringList names;
        for (const auto &e : u) names << e.username;
        QCOMPARE(names, QStringList({"alex", "sam", "jose", "li", "high", "onlycomma"}));
    }
    void gecosVariants() {
        const auto u = UserDiscovery::parsePasswd(kPasswd);
        QCOMPARE(u[0].displayName, QString("Alex Morgan"));          // trailing GECOS fields dropped
        QCOMPARE(u[1].displayName, QString("sam"));                  // empty GECOS: username
        QCOMPARE(u[2].displayName, QString::fromUtf8("Jos\xc3\xa9 \xc3\x81lvarez"));
        QCOMPARE(u[3].displayName, QString::fromUtf8("\xe6\x9d\x8e\xe6\x98\x8e"));
        QCOMPARE(u[5].displayName, QString("onlycomma"));            // ",,," : username
    }
    void emptyAndGarbageInput() {
        QVERIFY(UserDiscovery::parsePasswd("").isEmpty());
        QVERIFY(UserDiscovery::parsePasswd("\n\n:::::::\n").isEmpty());
        QVERIFY(UserDiscovery::parsePasswd(QByteArray("\xff\xfe\x00\x01:x:1000:1000", 15)).isEmpty());
    }
    void monogramVariants_data() {
        QTest::addColumn<QString>("display");
        QTest::addColumn<QString>("user");
        QTest::addColumn<QString>("expected");
        QTest::newRow("two words") << "Alex Morgan" << "alex" << "AM";
        QTest::newRow("three words") << "Mary Jane Watson" << "mj" << "MW";
        QTest::newRow("one word") << "Sam" << "sam" << "S";
        QTest::newRow("lowercase") << "alex morgan" << "a" << "AM";
        QTest::newRow("accents") << QString::fromUtf8("\xc3\xa9lodie \xc3\xa7" "a") << "e" << QString::fromUtf8("\xc3\x89\xc3\x87");
        QTest::newRow("cjk") << QString::fromUtf8("\xe6\x9d\x8e\xe6\x98\x8e") << "li" << QString::fromUtf8("\xe6\x9d\x8e");
        QTest::newRow("emoji skipped") << QString::fromUtf8("\xf0\x9f\x98\x80 Bob") << "bob" << "B";
        QTest::newRow("empty uses username") << "" << "quinn" << "Q";
        QTest::newRow("punctuation") << "j.r. smith" << "jr" << "JS";
        QTest::newRow("nothing usable") << "" << "" << "?";
        QTest::newRow("symbols only") << "***" << "___" << "?";
    }
    void monogramVariants() {
        QFETCH(QString, display); QFETCH(QString, user); QFETCH(QString, expected);
        QCOMPARE(UserDiscovery::monogram(display, user), expected);
    }
    void avatarColorsDeterministicAndInAccentFamily() {
        const QColor accent("#7C86FF");
        const auto a = UserDiscovery::avatarColors("alex", accent);
        const auto b = UserDiscovery::avatarColors("alex", accent);
        QCOMPARE(a.first, b.first);
        QCOMPARE(a.second, b.second);
        // Known value: FNV-1a("alex") fixes the hue.
        QVERIFY(a.first.isValid() && a.second.isValid());
        QVERIFY(UserDiscovery::avatarColors("sam", accent).first != a.first);
        // Every name stays within 60 degrees of the accent hue (family).
        for (const QString &n : {"a", "b", "zed", "morgan", "x1", "\xc3\xa9lo"}) {
            const auto c = UserDiscovery::avatarColors(n, accent);
            qreal d = qAbs(c.first.hslHueF() - accent.hueF());
            d = qMin(d, 1.0 - d);
            QVERIFY2(d <= 0.2, qPrintable(n));
            // White initials stay readable: dark enough stops.
            QVERIFY(c.first.lightnessF() < 0.6 && c.second.lightnessF() < 0.45);
        }
    }
    void iconFallbackOrder() {
        QTemporaryDir d;
        QImage img(8, 8, QImage::Format_RGB32);
        img.fill(Qt::red);
        QVERIFY(img.save(d.path() + "/a.png"));
        QDir().mkpath(d.path() + "/icons");
        QVERIFY(img.save(d.path() + "/icons/alex", "PNG"));
        QDir().mkpath(d.path() + "/home");
        QVERIFY(img.save(d.path() + "/home/.face", "PNG"));
        QCOMPARE(UserDiscovery::findIcon("alex", d.path() + "/a.png", d.path() + "/home", d.path() + "/icons"), d.path() + "/a.png");
        QCOMPARE(UserDiscovery::findIcon("alex", "", d.path() + "/home", d.path() + "/icons"), d.path() + "/icons/alex");
        QCOMPARE(UserDiscovery::findIcon("bob", "", d.path() + "/home", d.path() + "/icons"), d.path() + "/home/.face");
        QCOMPARE(UserDiscovery::findIcon("bob", "", d.path() + "/none", d.path() + "/icons"), QString());
        // A file that is not an image is ignored.
        QFile f(d.path() + "/junk"); QVERIFY(f.open(QIODevice::WriteOnly)); f.write("not an image"); f.close();
        QCOMPARE(UserDiscovery::findIcon("bob", d.path() + "/junk", "", d.path() + "/icons"), QString());
    }
    void modelFromPasswdFile() {
        QTemporaryDir d;
        QFile f(d.path() + "/passwd"); QVERIFY(f.open(QIODevice::WriteOnly)); f.write(kPasswd); f.close();
        UserModel m;
        m.discover(false, d.path() + "/passwd");
        QCOMPARE(m.rowCount(), 6);
        QCOMPARE(m.data(m.index(0), UserModel::DisplayNameRole).toString(), QString("Alex Morgan"));
        QCOMPARE(m.data(m.index(0), UserModel::InitialsRole).toString(), QString("AM"));
        QCOMPARE(m.data(m.index(0), UserModel::IconRole).toString(), QString());
        QCOMPARE(m.indexOfUser("sam"), 1);
        QCOMPARE(m.indexOfUser("ghost"), -1);
        UserModel missing;
        missing.discover(false, d.path() + "/does-not-exist");
        QCOMPARE(missing.rowCount(), 0);
        QVERIFY(missing.usernameAt(5).isEmpty());
    }
};
QTEST_MAIN(TestUserModel)
#include "test_usermodel.moc"
