// SPDX-License-Identifier: AGPL-3.0-or-later
// A tiny Wayland window for the integration test: taos-window-dummy <app_id> <title> [width height [minimise-after-ms]]
// With the last argument the window asks to be minimised (xdg_toplevel.set_minimized, as a foreign app's own button does).
#include <QGuiApplication>
#include <QPainter>
#include <QRasterWindow>
#include <QTimer>

namespace {
class W : public QRasterWindow {
protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);
        p.fillRect(QRect(0, 0, width(), height()), QColor(60, 90, 100));
    }
};
}  // namespace

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    const QStringList a = app.arguments();
    QGuiApplication::setDesktopFileName(a.value(1, QStringLiteral("taos.dummy")));
    W w;
    w.setTitle(a.value(2, QStringLiteral("dummy")));
    w.resize(a.value(3, QStringLiteral("300")).toInt(), a.value(4, QStringLiteral("200")).toInt());
    w.show();
    if (a.size() > 5)
        QTimer::singleShot(a.at(5).toInt(), &w, [&w] { w.showMinimized(); });
    return app.exec();
}
