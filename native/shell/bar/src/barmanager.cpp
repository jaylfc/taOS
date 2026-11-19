#include "barmanager.h"

#include <LayerShellQt/Window>
#include <QGuiApplication>
#include <QQuickView>
#include <QScreen>
#include <QUrl>
#include <QVariantMap>
#include <cstdio>

BarManager::BarManager(const Options &o, QObject *parent) : QObject(parent), m_o(o) {}

BarManager::~BarManager() {
    for (QQuickView *v : std::as_const(m_views)) {
        v->destroy();
        delete v;
    }
}

QString BarManager::describeOutputs() const {
    QStringList l;
    for (QScreen *s : QGuiApplication::screens())
        l << QStringLiteral("%1(%2x%3@%4)").arg(s->name()).arg(s->geometry().width()).arg(s->geometry().height()).arg(s->devicePixelRatio());
    return l.join(QLatin1Char(','));
}

void BarManager::start() {
    connect(qGuiApp, &QGuiApplication::screenAdded, this, &BarManager::addScreen);
    connect(qGuiApp, &QGuiApplication::screenRemoved, this, &BarManager::removeScreen);
    for (QScreen *s : QGuiApplication::screens()) addScreen(s);
}

void BarManager::addScreen(QScreen *s) {
    if (m_views.contains(s)) return;
    if (!m_o.onlyOutput.isEmpty() && s->name() != m_o.onlyOutput) return;

    auto *v = new QQuickView(&m_engine, nullptr);
    v->setScreen(s);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-bar"));
    v->setInitialProperties({{QStringLiteral("barHeight"), m_o.height},
                             {QStringLiteral("outputName"), s->name()}});
    v->resize(s->geometry().width(), m_o.height);
    v->setSource(QUrl(m_o.qmlUrl));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-bar: qml: %s\n", qPrintable(e.toString()));
        delete v;
        return;
    }
    v->create();  // platform window must exist before LayerShellQt can configure it
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(s);  // explicit: otherwise a hot-plugged output can end up with the surface on the first one
        w->setLayer(LayerShellQt::Window::LayerTop);
        w->setScope(QStringLiteral("taos-bar"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop
                                                    | LayerShellQt::Window::AnchorLeft
                                                    | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(m_o.height);
        w->setDesiredSize(QSize(0, m_o.height));
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
    } else {
        fprintf(stderr, "taos-bar: layer shell is not available on output %s (not a layer-shell compositor?)\n",
                qPrintable(s->name()));
    }
    v->show();
    m_views.insert(s, v);
    fprintf(stderr, "taos-bar: bar added on output %s\n", qPrintable(s->name()));
}

void BarManager::removeScreen(QScreen *s) {
    QQuickView *v = m_views.take(s);
    if (!v) return;
    fprintf(stderr, "taos-bar: bar removed from output %s\n", qPrintable(s->name()));
    v->destroy();
    delete v;
}
