// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QImage>
#include <QTemporaryDir>
#include <QtTest>

#include "iconresolve.h"

namespace {
DesktopEntry entry(const QString &id, const QByteArray &text) {
    DesktopEntry e;
    const bool ok = Desktop::parse("[Desktop Entry]\nType=Application\n" + text, id, &e);
    Q_ASSERT(ok);
    Q_UNUSED(ok);
    return e;
}
}  // namespace

class TestIcons : public QObject {
    Q_OBJECT
    QTemporaryDir dir;
private slots:
    // The owner's case: an app-group app (Claude) whose window says "Claude" while its launcher says com.anthropic.Claude.
    void appGroupAppFoundByProductName() {
        const QString png = dir.filePath(QStringLiteral("claude-desktop.png"));
        QImage img(64, 64, QImage::Format_ARGB32);
        img.fill(Qt::red);
        QVERIFY(img.save(png));
        AppInfo apps;
        apps.setEntries({ entry(QStringLiteral("taos-appgroups-claude-desktop"),
                                "Name=Claude\nExec=/home/x/.local/bin/taos-appgroups run claude-desktop %U\nIcon=" + png.toUtf8()
                                    + "\nStartupWMClass=com.anthropic.Claude\nX-taOS-AppId=com.anthropic.Claude\n") });
        for (const char *id : { "Claude", "claude", "com.anthropic.Claude" }) {
            QVERIFY2(!Icons::resolve(&apps, QString::fromLatin1(id)).isNull(), id);
            QCOMPARE(apps.displayName(QString::fromLatin1(id)), QStringLiteral("Claude"));
        }
        QVERIFY(Icons::resolve(&apps, QStringLiteral("nothing-like-it")).isNull());
    }
    void matchesLikeTheDock() {
        AppInfo apps;
        apps.setEntries({ entry(QStringLiteral("org.gnome.Nautilus"), "Name=Files\nExec=nautilus\nIcon=/nope.png\n"),
                          entry(QStringLiteral("fm"), "Name=Manager\nExec=env A=1 /usr/bin/pcmanfm %U\nIcon=x\n"),
                          entry(QStringLiteral("web"), "Name=Web\nExec=web\nIcon=x\nX-taOS-AppId=taos.web.gh1\n") });
        QCOMPARE(apps.displayName(QStringLiteral("nautilus")), QStringLiteral("Files"));     // last segment of a reverse-DNS id
        QCOMPARE(apps.displayName(QStringLiteral("pcmanfm")), QStringLiteral("Manager"));    // Exec's program, past env
        QCOMPARE(apps.displayName(QStringLiteral("taos.web.gh1")), QStringLiteral("Web"));   // X-taOS-AppId
        QCOMPARE(apps.displayName(QStringLiteral("some.unknown.thing")), QStringLiteral("Thing"));
    }
    void bareHicolorTreeWithoutIndexTheme() {
        QVERIFY(QDir().mkpath(dir.filePath(QStringLiteral("icons/hicolor/scalable/apps"))));
        QFile f(dir.filePath(QStringLiteral("icons/hicolor/scalable/apps/barething.svg")));
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16'><rect width='16' height='16' fill='red'/></svg>");
        f.close();
        QIcon::setThemeSearchPaths({ dir.filePath(QStringLiteral("icons")) });
        AppInfo apps;
        apps.setEntries({ entry(QStringLiteral("bare"), "Name=Bare\nExec=bare\nIcon=barething\n") });
        QVERIFY(!Icons::resolve(&apps, QStringLiteral("bare")).isNull());
    }
};
QTEST_MAIN(TestIcons)
#include "test_icons.moc"
