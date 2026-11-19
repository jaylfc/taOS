#include "overlayhost.h"

#include <LayerShellQt/Window>
#include <QGuiApplication>
#include <QScreen>

OverlayHost::OverlayHost(QQmlEngine *engine, Controller *c, QObject *parent) : QObject(parent), m_engine(engine), m_c(c) {
    connect(c, &Controller::showWindow, this, &OverlayHost::show);
    connect(c, &Controller::hideWindow, this, &OverlayHost::hide);
    connect(c, &Controller::releaseKeyboard, this, [this] {
        if (!m_view) return;
        if (auto *w = LayerShellQt::Window::get(m_view)) w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
        m_view->requestUpdate();
    });
    m_activateGuard.setSingleShot(true);
    m_activateGuard.setInterval(800);
    connect(&m_activateGuard, &QTimer::timeout, this, [this] {
        // The compositor never gave us the keyboard (so no Alt release can reach us): do not stay up.
        if (m_view && !m_view->isActive() && m_c->mode() == QLatin1String("switcher") && !m_c->sticky()) m_c->cancel();
    });
}

OverlayHost::~OverlayHost() {
    if (m_view) { m_view->destroy(); delete m_view; }
}

bool OverlayHost::load(const QUrl &qml, QString *error) {
    m_view = new QQuickView(m_engine, nullptr);
    m_view->setColor(Qt::transparent);
    m_view->setFlag(Qt::FramelessWindowHint);
    m_view->setResizeMode(QQuickView::SizeRootObjectToView);
    m_view->setTitle(QStringLiteral("taos-overview"));
    m_view->setPersistentSceneGraph(true);
    m_view->setSource(qml);
    if (m_view->status() != QQuickView::Ready) {
        QStringList l;
        for (const auto &e : m_view->errors()) l << e.toString();
        if (error) *error = l.join(QLatin1Char('\n'));
        return false;
    }
    connect(m_view, &QQuickView::activeChanged, this, [this] {
        if (!m_view->isActive()) return;
        m_activateGuard.stop();
        const bool alt = QGuiApplication::queryKeyboardModifiers() & Qt::AltModifier;
        m_c->windowActivated(alt);
    });
    return true;
}

void OverlayHost::configure(QScreen *s, bool exclusive) {
    if (auto *w = LayerShellQt::Window::get(m_view)) {
        w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-overview"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom
                                                    | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(-1);   // cover the bar too
        w->setDesiredSize(QSize(0, 0));
        w->setKeyboardInteractivity(exclusive ? LayerShellQt::Window::KeyboardInteractivityExclusive
                                              : LayerShellQt::Window::KeyboardInteractivityNone);
    } else {
        fprintf(stderr, "taos-overview: layer shell is not available (not a layer-shell compositor?)\n");
    }
}

void OverlayHost::show(const QString &output) {
    if (!m_view) return;
    QScreen *s = nullptr;
    for (QScreen *c : QGuiApplication::screens()) if (c->name() == output) s = c;
    if (!s) s = QGuiApplication::primaryScreen();
    if (!s) return;
    if (m_view->isVisible()) {   // reopened while fading out: it is still up
        m_c->noteFirstFrame();
        return;
    }
    if (m_view->screen() != s) m_view->setScreen(s);
    m_c->setDevicePixelRatio(s->devicePixelRatio());
    m_view->resize(s->geometry().size());
    m_view->create();
    configure(s, true);
    if (m_frameConn) disconnect(m_frameConn);
    m_frameConn = connect(m_view, &QQuickWindow::frameSwapped, this, [this] {
        disconnect(m_frameConn);
        m_c->noteFirstFrame();
    });
    m_view->show();
    if (m_c->mode() == QLatin1String("switcher") && !m_c->sticky()) m_activateGuard.start();
}

void OverlayHost::hide() {
    if (!m_view) return;
    m_activateGuard.stop();
    if (m_frameConn) disconnect(m_frameConn);
    m_view->hide();
    m_c->noteHidden();
}
