// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/windowcontrol.h"

#include <QCoreApplication>
#include <QGuiApplication>

#include "core/swayconn.h"
#include "core/windowops.h"

namespace Taos {

WindowControl::WindowControl(QObject *parent) : QObject(parent) {}

void WindowControl::setWindow(QWindow *w)
{
    if (m_window == w)
        return;
    m_window = w;
    emit windowChanged();
}

bool WindowControl::underSway() const
{
    return !TaosWin::SwayConnection::socketPath().isEmpty();
}

// Runs `act` on this window's container through the engine. False when not under sway or the engine failed.
template <class F> static bool viaEngine(F act)
{
    if (TaosWin::SwayConnection::socketPath().isEmpty())
        return false;
    TaosWin::SwayTransport tx;
    TaosWin::WindowController c(&tx);
    QString err;
    const int id = c.ownWindowId(int(QCoreApplication::applicationPid()), QGuiApplication::desktopFileName(), &err);
    if (id <= 0)
        return false;
    return act(c, QString::number(id), &err);
}

void WindowControl::close()
{
    if (viaEngine([](TaosWin::WindowController &c, const QString &id, QString *e) { return c.close(id, e); }))
        return;
    if (m_window)
        m_window->close();
}

void WindowControl::minimise()
{
    if (viaEngine([](TaosWin::WindowController &c, const QString &id, QString *e) { return c.minimise(id, e); }))
        return;
    if (m_window)
        m_window->showMinimized();
}

void WindowControl::toggleFull()
{
    if (viaEngine([](TaosWin::WindowController &c, const QString &id, QString *e) { return c.full(id, e); }))
        return;
    if (!m_window)
        return;
    if (m_window->visibility() == QWindow::Maximized)
        m_window->showNormal();
    else
        m_window->showMaximized();
}

} // namespace Taos
