// SPDX-License-Identifier: AGPL-3.0-or-later
#include "dockmanager.h"

#include <LayerShellQt/Window>
#include <QGuiApplication>
#include <QQuickItem>
#include <QQuickView>
#include <QScreen>
#include <cstdio>

#include "theme.h"

DockManager::DockManager(DockController *c, QQmlEngine *engine, Theme *theme, QObject *parent)
    : QObject(parent), m_c(c), m_engine(engine), m_theme(theme) {}

bool DockManager::taskbar() const { return m_theme && m_theme->dockMode() == QLatin1String("taskbar"); }   // "cells" is the floating dock, drawn differently

// The taskbar spans the output; with no top bar the far end belongs to the tray (taos-bar's surface).
QRect DockManager::taskbarRect(const QSize &out) const {
    const int h = m_theme->taskbarHeight();
    const int tray = m_theme->topBar() ? 0 : m_theme->trayWidth();
    const int y = m_theme->dockEdge() == QLatin1String("top") ? 0 : out.height() - h;
    return QRect(0, y, out.width() - tray, h);
}

void DockManager::rebuild() {
    closeMenu();
    hideTip();
    for (QQuickView *v : std::as_const(m_views)) { v->destroy(); delete v; }
    m_views.clear();
    for (QScreen *s : QGuiApplication::screens()) addScreen(s);
}

DockManager::~DockManager() {
    if (m_menu) { m_menu->destroy(); delete m_menu; }
    if (m_tip) { m_tip->destroy(); delete m_tip; }
    for (QQuickView *v : std::as_const(m_views)) { v->destroy(); delete v; }
}

bool DockManager::wanted(QScreen *s) const {
    if (m_c->settings()->outputs() == QLatin1String("all")) return true;
    return s == QGuiApplication::primaryScreen();
}

void DockManager::start() {
    if (m_theme) connect(m_theme, &Theme::layoutChanged, this, &DockManager::rebuild);
    connect(m_c, &DockController::layoutChanged, this, &DockManager::apply);
    connect(m_c, &DockController::revealedChanged, this, &DockManager::apply);
    connect(m_c, &DockController::menuRequested, this, &DockManager::openMenu);
    connect(m_c, &DockController::shrunkChanged, this, &DockManager::apply);
    connect(m_c, &DockController::tipRequested, this, &DockManager::showTip);
    connect(m_c, &DockController::tipHidden, this, &DockManager::hideTip);
    connect(m_c, &DockController::keyboardActiveChanged, this, &DockManager::apply);
    connect(m_c, &DockController::settingsRequested, this, &DockManager::openSettings);
    connect(m_c, &DockController::menuClosed, this, &DockManager::closeMenu);
    connect(m_c->settings(), &DockSettings::changed, this, [this] {
        for (QScreen *s : QGuiApplication::screens()) {
            if (wanted(s) && !m_views.contains(s)) addScreen(s);
            else if (!wanted(s) && m_views.contains(s)) removeScreen(s);
        }
    });
    connect(qGuiApp, &QGuiApplication::screenAdded, this, &DockManager::addScreen);
    connect(qGuiApp, &QGuiApplication::screenRemoved, this, &DockManager::removeScreen);
    connect(qGuiApp, &QGuiApplication::primaryScreenChanged, this, [this] {
        for (QScreen *s : QGuiApplication::screens())
            if (wanted(s) && !m_views.contains(s)) addScreen(s);
    });
    for (QScreen *s : QGuiApplication::screens()) addScreen(s);
}

void DockManager::addScreen(QScreen *s) {
    if (m_views.contains(s) || !wanted(s)) return;
    auto *v = new QQuickView(m_engine, nullptr);
    v->setScreen(s);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-dock"));
    m_c->setOutputSize(s->geometry().size());
    v->resize(taskbar() ? taskbarRect(s->geometry().size()).size() : m_c->layout().surface);
    v->setSource(QUrl(taskbar() ? QStringLiteral("qrc:/taos/dock/qml/Taskbar.qml") : QStringLiteral("qrc:/taos/dock/qml/Dock.qml")));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-dock: qml: %s\n", qPrintable(e.toString()));
        delete v;
        return;
    }
    v->create();
    configure(v, s);
    v->show();
    m_views.insert(s, v);
    fprintf(stderr, "taos-dock: dock added on output %s\n", qPrintable(s->name()));
}

void DockManager::removeScreen(QScreen *s) {
    if (s == m_menuScreen) closeMenu();
    QQuickView *v = m_views.take(s);
    if (!v) return;
    v->destroy();
    delete v;
}

void DockManager::configure(QQuickView *v, QScreen *s) {
    if (taskbar()) {
        const QRect r = taskbarRect(s->geometry().size());
        const bool bottom = m_theme->dockEdge() != QLatin1String("top");
        const int tray = m_theme->topBar() ? 0 : m_theme->trayWidth();
        if (auto *w = LayerShellQt::Window::get(v)) {
            w->setScreen(s);
            w->setLayer(LayerShellQt::Window::LayerTop);
            w->setScope(QStringLiteral("taos-taskbar"));   // its own scope: the dock's layer effects (corner radius, blur) do not touch it
            w->setAnchors(LayerShellQt::Window::Anchors((bottom ? LayerShellQt::Window::AnchorBottom : LayerShellQt::Window::AnchorTop)
                                                        | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
            w->setMargins(QMargins(0, 0, tray, 0));
            w->setExclusiveZone(r.height());
            w->setDesiredSize(QSize(0, r.height()));
            w->setKeyboardInteractivity(m_c->keyboardActive() ? LayerShellQt::Window::KeyboardInteractivityExclusive
                                                              : LayerShellQt::Window::KeyboardInteractivityNone);
        }
        v->resize(r.size());
        return;
    }
    const Layout &l = m_c->layout();
    // The surface is exactly the strip (the gap to the screen edge is a layer-shell margin, not transparent
    // padding), so nothing around the strip is blurred or covered. Auto-hidden: just a thin line on the edge.
    const bool hidden = m_c->autohide() && !m_c->revealed() && m_c->shrunk();
    const QSize size = hidden ? l.hiddenSurface : l.surface;
    const int margin = hidden ? 0 : l.margin;
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerTop);
        w->setScope(QStringLiteral("taos-dock"));
        LayerShellQt::Window::Anchors a;
        if (l.anchorBottom) a |= LayerShellQt::Window::AnchorBottom;
        if (l.anchorLeft) a |= LayerShellQt::Window::AnchorLeft;
        if (l.anchorRight) a |= LayerShellQt::Window::AnchorRight;
        w->setAnchors(a);
        w->setMargins(l.anchorBottom ? QMargins(0, 0, 0, margin) : l.anchorLeft ? QMargins(margin, 0, 0, 0) : QMargins(0, 0, margin, 0));
        w->setExclusiveZone(l.exclusiveZone);
        w->setDesiredSize(size);
        // Only Super+D takes the keyboard (until Escape); otherwise the dock never steals focus.
        w->setKeyboardInteractivity(m_c->keyboardActive() ? LayerShellQt::Window::KeyboardInteractivityExclusive
                                                          : LayerShellQt::Window::KeyboardInteractivityNone);
    }
    v->resize(size);
}

void DockManager::apply() {
    for (auto it = m_views.begin(); it != m_views.end(); ++it) configure(it.value(), it.key());
}

void DockManager::openMenu(qreal x, qreal y, int) { openOverlay(QStringLiteral("DockMenu.qml"), QPointF(x, y)); }

void DockManager::openSettings() {
    const QRect s = taskbar() ? taskbarRect(QSize(1920, 1080)).translated(0, -taskbarRect(QSize(1920, 1080)).top()) : m_c->layout().strip;
    openOverlay(QStringLiteral("DockSettings.qml"), QPointF(s.center()));
}

void DockManager::openOverlay(const QString &qml, const QPointF &p) {
    closeMenu();
    QScreen *s = m_views.isEmpty() ? QGuiApplication::primaryScreen() : m_views.constBegin().key();
    if (!s) return;
    const Layout &l = m_c->layout();
    const QPoint origin = taskbar() ? taskbarRect(s->geometry().size()).topLeft() : l.surfacePos(s->geometry().size());
    auto *v = new QQuickView(m_engine, nullptr);
    v->setScreen(s);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-dock-overlay"));
    QVariantMap props{{QStringLiteral("anchorX"), origin.x() + p.x()}, {QStringLiteral("anchorY"), origin.y() + p.y()},
                      {QStringLiteral("edge"), m_c->position()}};
    if (taskbar()) {
        props[QStringLiteral("edge")] = m_theme->dockEdge();
        props[QStringLiteral("clearance")] = m_theme->taskbarHeight() / 2 + 6;
    }
    v->setInitialProperties(props);
    v->resize(s->geometry().size());
    v->setSource(QUrl(QStringLiteral("qrc:/taos/dock/qml/") + qml));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-dock: overlay qml: %s\n", qPrintable(e.toString()));
        delete v;
        m_c->closeMenu();
        return;
    }
    v->create();
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-dock-menu"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom
                                                    | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(-1);
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityExclusive);
    }
    v->show();
    m_menu = v;
    m_menuScreen = s;
}

void DockManager::closeMenu() {
    if (!m_menu) return;
    QQuickView *v = m_menu;
    m_menu = nullptr;
    m_menuScreen = nullptr;
    v->hide();
    v->deleteLater();
}

// Tooltips live in their own small surface (sized to the text, input-transparent), so they never
// enlarge the dock's surface or the area swayfx blurs behind it.
void DockManager::showTip(const QString &text, qreal x, qreal y) {
    hideTip();
    if (text.isEmpty() || m_views.isEmpty()) return;
    QScreen *s = m_views.constBegin().key();
    const Layout &l = m_c->layout();
    const QSize out = s->geometry().size();
    const QPoint origin = taskbar() ? taskbarRect(out).topLeft() : l.surfacePos(out);
    auto *v = new QQuickView(m_engine, nullptr);
    v->setScreen(s);
    v->setColor(Qt::transparent);
    v->setFlags(Qt::FramelessWindowHint | Qt::WindowTransparentForInput);
    v->setResizeMode(QQuickView::SizeViewToRootObject);
    v->setTitle(QStringLiteral("taos-dock-tip"));
    v->setInitialProperties({{QStringLiteral("text"), text}});
    v->setSource(QUrl(QStringLiteral("qrc:/taos/dock/qml/Tip.qml")));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-dock: tip qml: %s\n", qPrintable(e.toString()));
        delete v;
        return;
    }
    const QSize ts(int(v->rootObject()->implicitWidth()), int(v->rootObject()->implicitHeight()));
    const QPointF c(origin.x() + x, origin.y() + y);
    QPoint p;
    const QRect strip = taskbar() ? taskbarRect(out) : l.strip.translated(origin);
    if (taskbar()) p = QPoint(int(c.x()) - ts.width() / 2, m_theme->dockEdge() == QLatin1String("top") ? strip.bottom() + 6 : strip.top() - ts.height() - 6);
    else if (l.anchorBottom) p = QPoint(int(c.x()) - ts.width() / 2, strip.top() - ts.height() - 6);
    else if (l.anchorLeft) p = QPoint(strip.right() + 7, int(c.y()) - ts.height() / 2);
    else p = QPoint(strip.left() - ts.width() - 6, int(c.y()) - ts.height() / 2);
    p.setX(qBound(4, p.x(), out.width() - ts.width() - 4));
    p.setY(qBound(4, p.y(), out.height() - ts.height() - 4));
    v->resize(ts);
    v->create();
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(s);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-dock-tip"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorLeft));
        w->setMargins(QMargins(p.x(), p.y(), 0, 0));
        w->setExclusiveZone(-1);
        w->setDesiredSize(ts);
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
    }
    v->show();
    m_tip = v;
}

void DockManager::hideTip() {
    if (!m_tip) return;
    QQuickView *v = m_tip;
    m_tip = nullptr;
    v->hide();
    v->deleteLater();
}
