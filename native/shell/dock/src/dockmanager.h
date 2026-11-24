#pragma once
#include <QHash>
#include <QObject>
#include <QQmlEngine>

#include "dockcontroller.h"

class QQuickView;
class QScreen;

// The layer-shell surfaces: one dock strip per output (the primary, or all) and the menu overlay.
class DockManager : public QObject {
    Q_OBJECT
public:
    DockManager(DockController *c, QQmlEngine *engine, QObject *parent = nullptr);
    ~DockManager() override;
    void start();
private slots:
    void apply();
    void openMenu(qreal x, qreal y, int row);
    void closeMenu();
    void openSettings();
    void showTip(const QString &text, qreal x, qreal y);
    void hideTip();
private:
    void openOverlay(const QString &qml, const QPointF &anchorInSurface);
    void addScreen(QScreen *s);
    void removeScreen(QScreen *s);
    void configure(QQuickView *v, QScreen *s);
    bool wanted(QScreen *s) const;
    DockController *m_c;
    QQmlEngine *m_engine;
    QHash<QScreen *, QQuickView *> m_views;
    QQuickView *m_menu = nullptr;
    QQuickView *m_tip = nullptr;
    QScreen *m_menuScreen = nullptr;
};
