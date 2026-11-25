// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QGuiApplication>
#include <QIcon>
#include <QImage>
#include <QtTest>

#include "iconprovider.h"
#include "rowmodel.h"

using namespace taos::launcher;

class TestIcons : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    static void svg(const QString &path) {
        QDir().mkpath(QFileInfo(path).absolutePath());
        QFile f(path);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect width='64' height='64' fill='#2f9e6f'/></svg>");
    }
private slots:
    void initTestCase() {
        // a bare icon tree with no index.theme: a build tree or a bare prefix, which the dock already copes with
        svg(m_tmp.path() + "/icons/hicolor/scalable/apps/bare-app.svg");
        svg(m_tmp.path() + "/icons/hicolor/scalable/apps/my.taos.settings.svg");
        svg(m_tmp.path() + "/pixmaps/legacy-app.svg");
        svg(m_tmp.path() + "/abs/direct.svg");
        QIcon::setThemeSearchPaths(QIcon::themeSearchPaths() << m_tmp.path() + "/icons");
        qputenv("TAOS_PIXMAP_DIRS", (m_tmp.path() + "/pixmaps").toUtf8());
        clearIconCache();
    }
    void absolutePathResolvesWhenTheFileExists() {
        QVERIFY(hasIcon(m_tmp.path() + "/abs/direct.svg"));
        QVERIFY(!hasIcon(m_tmp.path() + "/abs/missing.svg"));
    }
    void bareHicolorTreeResolvesLikeTheDock() {
        QVERIFY(hasIcon("bare-app"));
        QVERIFY(hasIcon("my.taos.settings"));
        QVERIFY(!resolveIcon("bare-app").isNull());
    }
    void caseInsensitiveRetry() { QVERIFY(hasIcon("Bare-App")); }
    void pixmapsFolderIsSearched() { QVERIFY(hasIcon("legacy-app")); }
    void unknownIsNotAnIconSoTheMenuDrawsItsFallback() {
        QVERIFY(!hasIcon("odd-missing-icon"));
        QVERIFY(!hasIcon(""));
        QCOMPARE(RowModel::iconUrl("odd-missing-icon"), QString());
    }
    void rowModelAndProviderAgree() {
        for (const QString &n : { QStringLiteral("bare-app"), QStringLiteral("legacy-app"), m_tmp.path() + "/abs/direct.svg" }) {
            const QString url = RowModel::iconUrl(n);
            QVERIFY2(!url.isEmpty(), qPrintable(n));
            IconProvider p;
            QSize sz;
            const QPixmap pm = p.requestPixmap(url.mid(QString("image://icon/").size()), &sz, QSize(48, 48));
            QVERIFY2(!pm.isNull() && pm.width() > 1, qPrintable(n));
            QCOMPARE(pm.toImage().pixelColor(pm.width() / 2, pm.height() / 2).green(), 0x9e);
        }
    }
    void theCacheIsClearedWhenAnAppAppears() {
        QVERIFY(!hasIcon("late-app"));
        svg(m_tmp.path() + "/icons/hicolor/scalable/apps/late-app.svg");
        clearIconCache();
        QVERIFY(hasIcon("late-app"));
    }
};

int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", "offscreen");
    QGuiApplication app(argc, argv);
    TestIcons t;
    return QTest::qExec(&t, argc, argv);
}
#include "launcher_test_icons.moc"
