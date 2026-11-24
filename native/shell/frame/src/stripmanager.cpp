// SPDX-License-Identifier: AGPL-3.0-or-later
#include "stripmanager.h"

#include <LayerShellQt/Window>
#include <QGuiApplication>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QScreen>
#include <cstdio>

#include "framecontroller.h"

StripManager::StripManager(FrameController *c, QQmlEngine *engine, QObject *parent) : QObject(parent), m_c(c), m_engine(engine)
{
    connect(c, &FrameController::stripsChanged, this, &StripManager::apply);
    connect(c, &FrameController::menuRequested, this, &StripManager::openMenu);
}

StripManager::~StripManager()
{
    closeMenu();
    for (QQuickView *v : std::as_const(m_views)) {
        v->destroy();
        delete v;
    }
}

int StripManager::liveViews() const
{
    int n = 0;
    for (QQuickView *v : m_views)
        n += v->isVisible() ? 1 : 0;
    return n;
}

QVariantList StripManager::buttonModel(bool right, const QString &title)
{
    QVariantList l;
    for (const StripLayout::Button &b : StripLayout::buttons(right))
        l.append(QVariantMap{{QStringLiteral("name"), b.name}, {QStringLiteral("x"), b.rect.x()}, {QStringLiteral("y"), b.rect.y()},
                             {QStringLiteral("w"), b.rect.width()}, {QStringLiteral("h"), b.rect.height()},
                             {QStringLiteral("label"), StripLayout::labelFor(b.name, title)}});
    return l;
}

QScreen *StripManager::screenFor(const QString &output) const
{
    for (QScreen *s : QGuiApplication::screens())
        if (s->name() == output)
            return s;
    // One screen and no name to match (a nested or offscreen test display): it is that one.
    const auto all = QGuiApplication::screens();
    return all.size() == 1 ? all.first() : nullptr;
}

QQuickView *StripManager::make(const Strip &s)
{
    auto *v = new QQuickView(m_engine, nullptr);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-frame-strip"));
    v->resize(s.size);
    v->setSource(QUrl(QStringLiteral("qrc:/taos/frame/qml/Strip.qml")));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-frame: qml: %s\n", qPrintable(e.toString()));
        delete v;
        return nullptr;
    }
    auto *bridge = new StripBridge(s.id, v);
    connect(bridge, &StripBridge::pressed, this, [this](int id, const QString &b) { m_c->perform(id, b); });
    connect(bridge, &StripBridge::menuAsked, this, &StripManager::openMenu);
    v->rootObject()->setProperty("bridge", QVariant::fromValue(static_cast<QObject *>(bridge)));
    return v;
}

void StripManager::place(QQuickView *v, const Strip &s, QScreen *screen)
{
    v->setScreen(screen);
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(screen);
        w->setLayer(LayerShellQt::Window::LayerTop);
        w->setScope(QStringLiteral("taos-frame"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorLeft));
        w->setMargins(QMargins(s.margin.x(), s.margin.y(), 0, 0));
        w->setExclusiveZone(-1);                  // sit where asked, whatever the bar and dock reserve
        w->setDesiredSize(s.size);
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);   // never take the keyboard
    }
    v->resize(s.size);
}

void StripManager::apply()
{
    QSet<int> wanted;
    for (const Strip &s : m_c->strips()) {
        wanted.insert(s.id);
        QQuickView *v = m_views.value(s.id);
        if (!s.show) {
            if (v && v->isVisible())
                v->hide();
            continue;
        }
        QScreen *screen = screenFor(s.output);
        if (!screen)
            continue;
        if (!v) {
            v = make(s);
            if (!v)
                continue;
            m_views.insert(s.id, v);
        }
        if (QQuickItem *root = v->rootObject()) {
            root->setProperty("buttons", buttonModel(s.right, s.title));
            root->setProperty("active", s.focused);
        }
        const bool moved = v->isVisible() && (v->property("stripMargin").toPoint() != s.margin);
        if (!v->isVisible() || moved || v->screen() != screen) {
            if (v->isVisible())
                v->hide();                        // layer-shell margins are set before the surface exists
            place(v, s, screen);
            v->setProperty("stripMargin", s.margin);
            v->show();
        }
    }
    // Windows that are gone take their view with them.
    for (auto it = m_views.begin(); it != m_views.end();) {
        if (wanted.contains(it.key())) {
            ++it;
            continue;
        }
        it.value()->destroy();
        delete it.value();
        it = m_views.erase(it);
    }
}

void StripManager::closeMenu()
{
    if (m_menu) {
        m_menu->destroy();
        m_menu->deleteLater();     // closeMenu runs from the menu's own signal
    }
    m_menu = nullptr;
}

void StripManager::openMenu(int id)
{
    closeMenu();
    const Strip *strip = nullptr;
    for (const Strip &s : m_c->strips())
        if (s.id == id)
            strip = &s;
    if (!strip)
        return;
    QScreen *screen = screenFor(strip->output);
    if (!screen)
        return;
    auto *v = new QQuickView(m_engine, nullptr);
    v->setScreen(screen);
    v->setColor(Qt::transparent);
    v->setFlag(Qt::FramelessWindowHint);
    v->setResizeMode(QQuickView::SizeRootObjectToView);
    v->setTitle(QStringLiteral("taos-frame-menu"));
    const qreal ax = strip->right ? strip->margin.x() + strip->size.width() - 168 : strip->margin.x();
    v->setInitialProperties({{QStringLiteral("anchorX"), ax}, {QStringLiteral("anchorY"), strip->margin.y() + strip->size.height() + 4},
                             {QStringLiteral("windowTitle"), strip->title}});
    v->resize(screen->geometry().size());
    v->setSource(QUrl(QStringLiteral("qrc:/taos/frame/qml/FrameMenu.qml")));
    if (v->status() != QQuickView::Ready) {
        for (const auto &e : v->errors()) fprintf(stderr, "taos-frame: menu qml: %s\n", qPrintable(e.toString()));
        delete v;
        return;
    }
    v->create();
    if (auto *w = LayerShellQt::Window::get(v)) {
        w->setScreen(screen);
        w->setLayer(LayerShellQt::Window::LayerOverlay);
        w->setScope(QStringLiteral("taos-frame-menu"));
        w->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom
                                                    | LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        w->setExclusiveZone(-1);
        w->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityExclusive);   // only while the menu is open
    }
    auto *bridge = new StripBridge(id, v);
    connect(bridge, &StripBridge::chosen, this, [this](int wid, const QString &b) { closeMenu(); m_c->perform(wid, b); });
    connect(bridge, &StripBridge::dismissed, this, [this] { closeMenu(); });
    v->rootObject()->setProperty("bridge", QVariant::fromValue(static_cast<QObject *>(bridge)));
    v->show();
    m_menu = v;
}
