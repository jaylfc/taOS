#pragma once
#include <QObject>
#include <QQmlEngine>
#include <QQuickView>

#include "controller.h"

namespace taos::launcher {

// Owns the one layer-shell surface (layer OVERLAY, all four edges, exclusive keyboard) on the focused
// output. The QML scene stays loaded while the window is hidden, so showing costs one surface commit.
class Host : public QObject {
    Q_OBJECT
public:
    Host(QQmlEngine *engine, Controller *c, QObject *parent = nullptr);
    ~Host() override;
    bool load(const QUrl &qml, QString *error);
    QQuickView *view() const { return m_view; }
private:
    void show();
    void hide();
    QQmlEngine *m_engine;
    Controller *m_c;
    QQuickView *m_view = nullptr;
};

}  // namespace taos::launcher
