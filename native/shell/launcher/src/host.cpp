#include "host.h"

#include <LayerShellQt/Window>
#include <QGuiApplication>
#include <QScreen>

#include "runner.h"

namespace taos::launcher {

Host::Host(QQmlEngine *engine, Controller *c, QObject *parent) : QObject(parent), m_engine(engine), m_c(c) {
    connect(c, &Controller::showWindow, this, &Host::show);
    connect(c, &Controller::hideWindow, this, &Host::hide);
    connect(qGuiApp, &QGuiApplication::aboutToQuit, this, [this] { if (m_view) m_view->hide(); });
}

Host::~Host() {
    if (m_view) { m_view->destroy(); delete m_view; }
}

bool Host::load(const QUrl &qml, QString *error) {
    m_view = new QQuickView(m_engine, nullptr);
    m_view->setColor(Qt::transparent);
    m_view->setFlag(Qt::FramelessWindowHint);
    m_view->setResizeMode(QQuickView::SizeRootObjectToView);
    m_view->setTitle(QStringLiteral("taos-launcher"));
    m_view->setPersistentSceneGraph(true);
    m_view->setSource(qml);
    if (m_view->status() != QQuickView::Ready) {
        QStringList l;
        for (const auto &e : m_view->errors()) l << e.toString();
        if (error) *error = l.join(QLatin1Char('\n'));
        return false;
    }
    connect(m_view, &QQuickView::activeChanged, this, [this] {
        if (m_view->isActive()) m_c->windowActivated();
        else if (m_view->isVisible()) m_c->windowDeactivated();   // focus loss closes the launcher
    });
    return true;
}

void Host::show() {
    if (!m_view || m_view->isVisible()) return;
    // The focused output. With one monitor there is nothing to ask; with several, ask sway (swaymsg, a few
    // ms) and fall back to the primary screen when that is not possible.
    QScreen *s = QGuiApplication::primaryScreen();
    if (QGuiApplication::screens().size() > 1) {
        const QString name = taos::search::focusedOutput();
        for (QScreen *c : QGuiApplication::screens()) if (!name.isEmpty() && c->name() == name) s = c;
    }
    if (s) {
        if (m_view->screen() != s) m_view->setScreen(s);
        m_view->resize(s->geometry().size());
    }
    m_view->create();
    if (auto *w = LayerShellQt::Window::get(m_view)) {
        if (s) w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-launcher"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom
                                                    | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(-1);
        w->setDesiredSize(QSize(0, 0));
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityExclusive);
    }
    m_view->show();
}

void Host::hide() {
    if (m_view) m_view->hide();
}

}  // namespace taos::launcher
