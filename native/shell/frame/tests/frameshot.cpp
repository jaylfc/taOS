// SPDX-License-Identifier: AGPL-3.0-or-later
// Dev tool: renders a framed window (sway-style title bar plus the strip) offscreen.
//   frameshot out.png [--right] [--light] [--theme id] [--hover close|minimise|full] [--inactive] [--title text]
#include <QGuiApplication>
#include <QImage>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QTemporaryDir>
#include <QTimer>
#include <QtQml>
#include <QtTest/QTest>

#include "frameplan.h"
#include "theme.h"
#include "thememanager.h"

int main(int argc, char **argv)
{
    qputenv("QT_QPA_PLATFORM", qgetenv("QT_QPA_PLATFORM").isEmpty() ? QByteArray("offscreen") : qgetenv("QT_QPA_PLATFORM"));
    if (qEnvironmentVariableIsEmpty("QT_QUICK_BACKEND")) qputenv("QT_QUICK_BACKEND", "software");
    QGuiApplication app(argc, argv);
    const QStringList a = app.arguments();
    if (a.size() < 2) { fprintf(stderr, "usage: frameshot out.png [options]\n"); return 2; }
    auto opt = [&](const char *k, const QString &def) { const int i = a.indexOf(QLatin1String(k)); return i > 0 && i + 1 < a.size() ? a.at(i + 1) : def; };
    const bool right = a.contains(QLatin1String("--right"));
    QTemporaryDir tmp;
    Theme theme;
    ThemeManager::Options to;
    to.pinnedTheme = opt("--theme", QStringLiteral("graphite"));
    to.pinnedVariant = a.contains(QLatin1String("--light")) ? QStringLiteral("light") : QStringLiteral("dark");
    to.watch = false;
    to.transitionMs = 0;
    to.appearancePath = tmp.path() + QStringLiteral("/appearance.json");
    ThemeManager themes(&theme, to);
    QString err;
    if (!themes.start(&err)) { fprintf(stderr, "theme: %s\n", qPrintable(err)); return 1; }
    theme.setWindowButtonsOverride(right ? QStringLiteral("right") : QStringLiteral("left"));

    qmlRegisterSingletonInstance("Taos.Frame", 1, 0, "Theme", &theme);
    QQmlEngine engine;
    QQuickView v(&engine, nullptr);
    v.setColor(Qt::black);
    v.setResizeMode(QQuickView::SizeRootObjectToView);
    const QString title = opt("--title", QStringLiteral("YouTube"));
    QVariantList model;
    for (const StripLayout::Button &b : StripLayout::buttons(right))
        model.append(QVariantMap{{QStringLiteral("name"), b.name}, {QStringLiteral("x"), b.rect.x()}, {QStringLiteral("y"), b.rect.y()},
                                 {QStringLiteral("w"), b.rect.width()}, {QStringLiteral("h"), b.rect.height()},
                                 {QStringLiteral("label"), StripLayout::labelFor(b.name, title)}});
    v.setInitialProperties({{QStringLiteral("title"), title}, {QStringLiteral("buttonsRight"), right}, {QStringLiteral("buttons"), model},
                            {QStringLiteral("active"), !a.contains(QLatin1String("--inactive"))}});
    v.resize(480, 120);
    v.setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_FRAME_QML_DIR "/../tests/scene.qml")));
    if (v.status() != QQuickView::Ready) {
        for (const auto &e : v.errors()) fprintf(stderr, "qml: %s\n", qPrintable(e.toString()));
        return 1;
    }
    v.show();
    QTest::qWait(150);
    const QString hover = opt("--hover", QString());
    if (!hover.isEmpty()) {
        QPoint at(40, 16);
        for (const StripLayout::Button &b : StripLayout::buttons(right))
            if (b.name == hover) at = (b.rect.center() + QPoint(right ? 480 - 8 - 80 : 8, 4));
        QTest::mouseMove(&v, at);
        QTest::qWait(250);
    }
    const QImage img = v.grabWindow();
    return img.save(a.at(1)) ? 0 : 1;
}
