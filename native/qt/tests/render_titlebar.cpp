// Renders the window title bar offscreen in its states: render_titlebar <out-dir> [width]
// Writes titlebar-<left|right>-<dark|light>-<normal|group-hover|close-hover|focus|inactive>.png
// (the side and the variant come from TAOS_APPEARANCE / TAOS_THEME_VARIANT, as in an app).
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickItem>
#include <QQuickWindow>
#include <QTest>
#include <functional>
#include <QtQml/qqml.h>

#include "glass.h"
#include "taosqt/appcontext.h"
#include "taosqt/qmlregister.h"
#include "theme.h"
#include "thememanager.h"

using namespace Qt::StringLiterals;

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    if (argc < 2)
        return 2;
    const QString outDir = QString::fromLocal8Bit(argv[1]);
    const int width = argc > 2 ? QString::fromLocal8Bit(argv[2]).toInt() : 420;
    Taos::ClientOptions options;
    Taos::Session session(options);
    Taos::registerQmlTypes();
    Taos::AppContext context(&session, QStringLiteral("my.taos.render"));
    Theme theme;
    ThemeManager::Options o;
    o.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    o.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    o.watch = false;
    ThemeManager themes(&theme, o);
    Style style(&theme);
    QString err;
    if (!themes.start(&err))
        return 3;
    context.setStyle(&style);
    QQmlApplicationEngine engine;
    context.setEngine(&engine);
    qmlRegisterSingletonInstance("Taos", 1, 0, "Taos", &context);
    engine.addImportPath(u"qrc:/taos/qml"_s);
    engine.loadData(QByteArrayLiteral(
        "import QtQuick\nimport QtQuick.Window\nimport Taos.Style 1.0\n"
        "Window { id: w; width: 420; height: 56; visible: true; color: TaosStyle.ground; title: 'Notifications'\n"
        "  TaosTitleBar { id: bar; objectName: 'bar'; window: w; width: parent.width } }\n"),
        QUrl(u"qrc:/render/Scene.qml"_s));
    auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().value(0));
    if (!win)
        return 4;
    win->resize(width, 56);
    win->show();
    win->requestActivate();
    QTest::qWait(300);
    const QString side = style.windowButtons();
    const QString variant = theme.variant();
    int bad = 0;
    auto shot = [&](const char *state) {
        QTest::qWait(250);
        const QString path = QStringLiteral("%1/titlebar-%2-%3-%4.png").arg(outDir, side, variant, QString::fromLatin1(state));
        if (!win->grabWindow().scaled(win->width() * 2, win->height() * 2, Qt::KeepAspectRatio, Qt::SmoothTransformation).save(path))
            ++bad;
    };
    auto *bar = win->findChild<QQuickItem *>(u"bar"_s);
    // The buttons: 24 px each, 4 px apart, 8 px in from their edge.
    const int first = side == QLatin1String("right") ? width - 8 - 3 * 24 - 8 : 8;
    auto buttonCentre = [&](int i) { return QPoint(first + i * 28 + 12, 16); };
    QTest::mouseMove(win, QPoint(width / 2, 50));
    shot("normal");
    QTest::mouseMove(win, buttonCentre(side == QLatin1String("right") ? 0 : 1));
    shot("group-hover");
    QTest::mouseMove(win, buttonCentre(side == QLatin1String("right") ? 2 : 0));
    shot("close-hover");
    QTest::mouseMove(win, QPoint(width / 2, 50));
    // keyboard focus on the first button (Tab), ring and glyph shown
    QTest::qWait(150);
    std::function<QQuickItem *(QQuickItem *)> firstButton = [&](QQuickItem *it) -> QQuickItem * {
        if (it->activeFocusOnTab() && it->property("glyph").isValid())
            return it;
        for (QQuickItem *c : it->childItems())
            if (QQuickItem *r = firstButton(c))
                return r;
        return nullptr;
    };
    if (bar)
        if (QQuickItem *k = firstButton(bar))
            k->forceActiveFocus(Qt::TabFocusReason);
    shot("focus");
    return bad ? 5 : 0;
}
