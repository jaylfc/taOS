// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QObject>
#include <QQmlEngine>
#include <QString>

class QQuickView;

class QScreen;
class MenuHost;
class Theme;

// BarManager: one layer-shell bar per output, created and destroyed as
// outputs appear and disappear.
class BarManager : public QObject {
    Q_OBJECT
public:
    struct Options {
        int height = 40;          // used when heightPinned (--height), else the theme's layout.barHeight
        bool heightPinned = false;
        Theme *theme = nullptr;   // layout and skin: which surfaces exist, where, how tall
        QString trayUrl;
        QString onlyOutput;  // empty: every output
        QString qmlUrl;
        QString networkMenuUrl, audioMenuUrl, displayMenuUrl, osdUrl;
        MenuHost *host = nullptr;
    };
    BarManager(const Options &o, QObject *parent = nullptr);
    ~BarManager() override;
    void start();
    int barCount() const { return m_views.size(); }
    int trayCount() const { return m_trays.size(); }
    int barHeight() const;
    QString describeOutputs() const;
    bool menuOpen() const { return m_menu != nullptr; }

public slots:
    void openMenu(const QString &kind, const QString &output, qreal anchorRight);
    // The on-screen level flash for the volume keys (kind: "volume" or "mic"; percent -1: no device).
    void showOsd(const QString &kind, int percent, bool muted);
    void closeMenu();

private:
    void rebuild();      // the layout changed: create, move or destroy the bar and tray surfaces
    void addScreen(QScreen *s);
    void addTray(QScreen *s);
    void removeScreen(QScreen *s);
    Options m_o;
    QQmlEngine m_engine;  // shared: singleton instances belong to one engine
    QHash<QScreen *, QQuickView *> m_views;
    QHash<QScreen *, QQuickView *> m_trays;   // taskbar layouts without a top bar: the tray, a surface of its own at the taskbar's end
    QQuickView *m_menu = nullptr;
    QString m_menuKind;
    QQuickView *m_osd = nullptr;
    QScreen *m_menuScreen = nullptr;
};
