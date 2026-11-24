// SPDX-License-Identifier: AGPL-3.0-or-later
// The strip's QML: what a click on each button emits, the right click, the accessible names, and that the button
// side follows the setting. Rendered offscreen; clicks are real mouse events on the window.
#include <QAccessible>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtQml>
#include <QtTest>

#include "frameplan.h"
#include "theme.h"
#include "thememanager.h"

class TestFrameQml : public QObject {
    Q_OBJECT
    Theme *m_theme = nullptr;
    ThemeManager *m_mgr = nullptr;
    QTemporaryDir m_tmp;
    QQmlEngine *m_engine = nullptr;

    static QVariantList model(bool right, const QString &title)
    {
        QVariantList l;
        for (const StripLayout::Button &b : StripLayout::buttons(right))
            l.append(QVariantMap{{QStringLiteral("name"), b.name}, {QStringLiteral("x"), b.rect.x()}, {QStringLiteral("y"), b.rect.y()},
                                 {QStringLiteral("w"), b.rect.width()}, {QStringLiteral("h"), b.rect.height()},
                                 {QStringLiteral("label"), StripLayout::labelFor(b.name, title)}});
        return l;
    }
private slots:
    void initTestCase()
    {
        m_theme = new Theme;
        ThemeManager::Options to;
        to.pinnedTheme = QStringLiteral("graphite");
        to.pinnedVariant = QStringLiteral("dark");
        to.watch = false;
        to.transitionMs = 0;
        to.appearancePath = m_tmp.path() + QStringLiteral("/appearance.json");
        m_mgr = new ThemeManager(m_theme, to);
        QString err;
        QVERIFY2(m_mgr->start(&err), qPrintable(err));
        qmlRegisterSingletonInstance("Taos.Frame", 1, 0, "Theme", m_theme);
        m_engine = new QQmlEngine;
    }
    void cleanupTestCase() { delete m_engine; delete m_mgr; delete m_theme; }

    void clicksReachTheRightButton_data()
    {
        QTest::addColumn<bool>("right");
        QTest::newRow("left") << false;
        QTest::newRow("right") << true;
    }
    void clicksReachTheRightButton()
    {
        QFETCH(bool, right);
        QQuickView v(m_engine, nullptr);
        v.setResizeMode(QQuickView::SizeRootObjectToView);
        v.setInitialProperties({{QStringLiteral("buttons"), model(right, QStringLiteral("Doc"))}});
        v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/Strip.qml")));
        QVERIFY2(v.status() == QQuickView::Ready, qPrintable(v.errors().value(0).toString()));
        v.resize(80, 24);
        v.show();
        QVERIFY(QTest::qWaitForWindowExposed(&v));
        QSignalSpy act(v.rootObject(), SIGNAL(activated(QString)));
        QSignalSpy ctx(v.rootObject(), SIGNAL(contextMenu()));
        for (const StripLayout::Button &b : StripLayout::buttons(right)) {
            QTest::mouseClick(&v, Qt::LeftButton, Qt::NoModifier, b.rect.center());
            QCOMPARE(act.size(), 1);
            QCOMPARE(act.takeFirst().at(0).toString(), b.name);
        }
        QTest::mouseClick(&v, Qt::LeftButton, Qt::NoModifier, QPoint(25, 12));    // the gap between two buttons
        QCOMPARE(act.size(), 0);
        QTest::mouseClick(&v, Qt::RightButton, Qt::NoModifier, QPoint(25, 12));
        QCOMPARE(ctx.size(), 1);
        QCOMPARE(act.size(), 0);
    }
    // A report: the buttons did not render until hovered. At rest every button shows its glyph, strongly in
    // a focused window and dimmer (still visible) in an unfocused one; a button the window cannot use is an empty disc.
    static int glyphContrast(const QImage &img, const QRect &hit)
    {
        const QColor fill = img.pixelColor(hit.x() + 6, hit.y() + 12);        // inside the 14 px disc, left of the glyph
        int best = 0;
        for (int y = hit.y() + 8; y <= hit.y() + 16; ++y)
            for (int x = hit.x() + 8; x <= hit.x() + 16; ++x)
                best = qMax(best, qAbs(img.pixelColor(x, y).lightness() - fill.lightness()));
        return best;
    }
    void glyphsAreVisibleAtRest()
    {
        QQuickView v(m_engine, nullptr);
        v.setColor(m_theme->surface());       // the bar the strip sits on
        v.setResizeMode(QQuickView::SizeRootObjectToView);
        v.setInitialProperties({{QStringLiteral("buttons"), model(false, QStringLiteral("Doc"))}, {QStringLiteral("active"), true}});
        v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/Strip.qml")));
        QVERIFY(v.status() == QQuickView::Ready);
        v.resize(80, 24);
        v.show();
        QVERIFY(QTest::qWaitForWindowExposed(&v));
        QTest::mouseMove(&v, QPoint(26, 12));                                   // in the gap between two buttons                                  // the pointer is nowhere near
        QTest::qWait(150);
        const QImage focused = v.grabWindow();
        v.rootObject()->setProperty("active", false);
        QTest::qWait(250);
        const QImage unfocused = v.grabWindow();
        for (const StripLayout::Button &b : StripLayout::buttons(false)) {
            const int f = glyphContrast(focused, b.rect), u = glyphContrast(unfocused, b.rect);
            QVERIFY2(f >= 40, qPrintable(QStringLiteral("%1: glyph contrast at rest %2 (focused)").arg(b.name).arg(f)));
            QVERIFY2(u >= 12, qPrintable(QStringLiteral("%1: glyph contrast at rest %2 (unfocused)").arg(b.name).arg(u)));
            QVERIFY2(u < f, "an unfocused window's buttons are dimmer");
        }
        QVariantList m = model(false, QStringLiteral("Doc"));
        QVariantMap full = m.at(2).toMap();
        full.insert(QStringLiteral("enabled"), false);
        m[2] = full;
        v.rootObject()->setProperty("buttons", m);
        v.rootObject()->setProperty("active", true);
        QTest::qWait(250);
        const QImage dis = v.grabWindow();
        QVERIFY2(glyphContrast(dis, StripLayout::buttons(false).at(2).rect) < 6, "a button the window cannot use is an empty disc");
        QVERIFY(glyphContrast(dis, StripLayout::buttons(false).at(0).rect) >= 40);
    }
    void hoverAndPressedLiftTheDisc()
    {
        QQuickView v(m_engine, nullptr);
        v.setColor(m_theme->surface());       // the bar the strip sits on
        v.setResizeMode(QQuickView::SizeRootObjectToView);
        v.setInitialProperties({{QStringLiteral("buttons"), model(false, QStringLiteral("Doc"))}});
        v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/Strip.qml")));
        QVERIFY(v.status() == QQuickView::Ready);
        v.resize(80, 24);
        v.show();
        QVERIFY(QTest::qWaitForWindowExposed(&v));
        const QRect mini = StripLayout::buttons(false).at(1).rect;
        QTest::mouseMove(&v, QPoint(26, 12));                                   // in the gap between two buttons
        QTest::qWait(150);
        const QColor rest = v.grabWindow().pixelColor(mini.x() + 6, mini.y() + 12);
        QTest::mouseMove(&v, mini.center());
        QTest::qWait(250);
        const QColor over = v.grabWindow().pixelColor(mini.x() + 6, mini.y() + 12);
        QVERIFY2(qAbs(over.lightness() - rest.lightness()) >= 8, "the hovered disc is lifted");
        const QRect close = StripLayout::buttons(false).at(0).rect;
        QTest::mouseMove(&v, close.center());
        QTest::qWait(250);
        const QColor danger = v.grabWindow().pixelColor(close.x() + 6, close.y() + 12);
        QVERIFY2(danger.red() > danger.blue() + 20, "the hovered close disc turns to the danger colour");
    }
    void buttonsAreNamedForTheWindow()
    {
        QQuickView v(m_engine, nullptr);
        v.setResizeMode(QQuickView::SizeRootObjectToView);
        v.setInitialProperties({{QStringLiteral("buttons"), model(false, QStringLiteral("Doc"))}});
        v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/Strip.qml")));
        QVERIFY(v.status() == QQuickView::Ready);
        v.resize(80, 24);
        v.show();
        QVERIFY(QTest::qWaitForWindowExposed(&v));
        QAccessible::setActive(true);
        QStringList names;
        for (QQuickItem *c : v.rootObject()->childItems()) {
            QAccessibleInterface *i = QAccessible::queryAccessibleInterface(c);
            if (i && i->role() == QAccessible::Button)
                names << i->text(QAccessible::Name);
        }
        QVERIFY2(names.contains(QStringLiteral("Close Doc")) && names.contains(QStringLiteral("Minimise Doc")) && names.contains(QStringLiteral("Full size Doc")),
                 qPrintable(names.join(QLatin1String(", "))));
    }
    void followsThemeSwitchLive()
    {
        QQuickView v(m_engine, nullptr);
        v.setResizeMode(QQuickView::SizeRootObjectToView);
        v.setInitialProperties({{QStringLiteral("buttons"), model(false, QStringLiteral("Doc"))}});
        v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/Strip.qml")));
        QVERIFY(v.status() == QQuickView::Ready);
        v.resize(80, 24);
        v.show();
        QVERIFY(QTest::qWaitForWindowExposed(&v));
        const QImage dark = v.grabWindow();
        // a light theme: the discs are drawn from the text colour, so the pixels change
        QString err;
        QVERIFY(m_theme->load(QStringLiteral("graphite"), QStringLiteral("light"), {}, QStringLiteral(":/taos/themes/graphite/theme.json"), false, &err));
        QTest::qWait(100);
        const QImage light = v.grabWindow();
        QVERIFY2(dark != light, "the strip did not repaint for the new theme");
        QVERIFY(m_theme->load(QStringLiteral("graphite"), QStringLiteral("dark"), {}, QStringLiteral(":/taos/themes/graphite/theme.json"), false, &err));
    }
};

QTEST_MAIN(TestFrameQml)
#include "test_frameqml.moc"
