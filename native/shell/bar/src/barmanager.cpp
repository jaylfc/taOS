#include "barmanager.h"

#include <LayerShellQt/Window>
#include "menuhost.h"
#include <QGuiApplication>
#include <QQuickItem>
#include <QQuickView>
#include <QScreen>
#include <QUrl>
#include <QVariantMap>
#include <cstdio>

BarManager::BarManager(const Options &o, QObject *parent) : QObject(parent), m_o(o) {}

BarManager::~BarManager() {
    if (m_menu) { m_menu->destroy(); delete m_menu; }
    if (m_osd) { m_osd->destroy(); delete m_osd; }
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

void BarManager::openMenu(const QString &kind, const QString &output, qreal anchorRight) {
    const QString url = kind == QLatin1String("audio") ? m_o.audioMenuUrl : m_o.networkMenuUrl;
    if (url.isEmpty()) return;
    // The same icon again closes it: the menu window covers the bar, so that click already closed it; a second open is ignored.
    closeMenu();
    QScreen *s = nullptr;
    for (QScreen *c : QGuiApplication::screens())
        if (c->name() == output) s = c;
    if (!s) s = QGuiApplication::primaryScreen();
    if (!s) return;
    auto *v = new QQuickView(&m_engine, nullptr);
    v->setScreen(s);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-menu"));
    v->setInitialProperties({{QStringLiteral("barHeight"), m_o.height}, {QStringLiteral("anchorRight"), anchorRight}});
    v->resize(s->geometry().size());
    v->setSource(QUrl(url));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-bar: menu qml: %s\n", qPrintable(e.toString()));
        delete v;
        return;
    }
    v->create();
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-menu"));
        // Whole output: the clear area behind the panel catches the click that dismisses the menu.
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom
                                                    | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(-1);
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityExclusive);
    } else {
        fprintf(stderr, "taos-bar: layer shell is not available for the network menu\n");
    }
    v->show();
    m_menu = v;
    m_menuScreen = s;
    m_menuKind = kind;
    if (m_o.host) m_o.host->setOpen(true, kind);
}

void BarManager::showOsd(const QString &kind, int percent, bool muted) {
    if (m_o.osdUrl.isEmpty()) return;
    if (!m_osd) {
        QScreen *s = QGuiApplication::primaryScreen();
        if (!s) return;
        auto *v = new QQuickView(&m_engine, nullptr);
        v->setScreen(s);
        v->setColor(Qt::transparent);
        v->setFlags(Qt::FramelessWindowHint | Qt::WindowTransparentForInput);  // clicks go to what is underneath
        v->setResizeMode(QQuickView::SizeRootObjectToView);
        v->setTitle(QStringLiteral("taos-osd"));
        v->resize(260, 84);
        v->setSource(QUrl(m_o.osdUrl));
        if (v->status() != QQuickView::Ready) {
            for (const auto &e : v->errors()) fprintf(stderr, "taos-bar: osd qml: %s\n", qPrintable(e.toString()));
            delete v;
            return;
        }
        v->create();
        if (auto *w = LayerShellQt::Window::get(v)) {
            w->setScreen(s);
            w->setLayer(LayerShellQt::Window::LayerOverlay);
            w->setScope(QStringLiteral("taos-osd"));
            w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorBottom));  // bottom centre
            w->setMargins(QMargins(0, 0, 0, 96));
            w->setExclusiveZone(-1);
            w->setDesiredSize(QSize(260, 84));
            w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
        }
        v->show();  // stays mapped and clear between flashes: no map/unmap churn
        m_osd = v;
    }
    QMetaObject::invokeMethod(static_cast<QObject *>(m_osd->rootObject()), "flash", Q_ARG(QVariant, kind), Q_ARG(QVariant, percent), Q_ARG(QVariant, muted));
}

void BarManager::closeMenu() {
    if (!m_menu) return;
    QQuickView *v = m_menu;
    m_menu = nullptr;
    m_menuScreen = nullptr;
    if (m_o.host) m_o.host->setOpen(false, m_menuKind);
    v->hide();
    v->deleteLater();  // may be called from inside the menu's own handler
}

void BarManager::start() {
    if (m_o.host) {
        connect(m_o.host, &MenuHost::openRequested, this, &BarManager::openMenu);
        connect(m_o.host, &MenuHost::closeFinished, this, &BarManager::closeMenu, Qt::QueuedConnection);
    }
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
    if (s == m_menuScreen) closeMenu();
    QQuickView *v = m_views.take(s);
    if (!v) return;
    fprintf(stderr, "taos-bar: bar removed from output %s\n", qPrintable(s->name()));
    v->destroy();
    delete v;
}
