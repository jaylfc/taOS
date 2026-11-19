#pragma once
#include <QObject>
#include <QQmlEngine>
#include <QQuickView>
#include <QTimer>

#include "controller.h"

// Owns the one layer-shell surface (layer OVERLAY, all four edges, exclusive keyboard) and shows it
// on the right output only while the overlay is up. The QML scene stays loaded while hidden.
class OverlayHost : public QObject {
    Q_OBJECT
public:
    OverlayHost(QQmlEngine *engine, Controller *c, QObject *parent = nullptr);
    ~OverlayHost() override;
    bool load(const QUrl &qml, QString *error);
private:
    void show(const QString &output);
    void hide();
    void configure(QScreen *s, bool exclusive);
    QQmlEngine *m_engine;
    Controller *m_c;
    QQuickView *m_view = nullptr;
    QMetaObject::Connection m_frameConn;
    QTimer m_activateGuard;
};
