// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QObject>
#include <QPointer>

#include "frameplan.h"

class FrameController;

// What a strip's (or the menu's) QML calls back into; one per view.
class StripBridge : public QObject {
    Q_OBJECT
public:
    StripBridge(int id, QObject *parent) : QObject(parent), m_id(id) {}
    int id() const { return m_id; }
    Q_INVOKABLE void press(const QString &button) { emit pressed(m_id, button); }
    Q_INVOKABLE void menu() { emit menuAsked(m_id); }
    Q_INVOKABLE void choose(const QString &button) { emit chosen(m_id, button); }
    Q_INVOKABLE void dismiss() { emit dismissed(); }
signals:
    void pressed(int id, const QString &button);
    void menuAsked(int id);
    void chosen(int id, const QString &button);
    void dismissed();
private:
    int m_id;
};
class QQmlEngine;
class QQuickView;
class QScreen;

// One small layer-shell surface per framed window, over the corner of its sway title bar where the three buttons go.
class StripManager : public QObject {
    Q_OBJECT
public:
    StripManager(FrameController *c, QQmlEngine *engine, QObject *parent = nullptr);
    ~StripManager() override;
    // Test and screenshot access: the view of a window's strip (null when none was made).
    QQuickView *viewFor(int id) const { return m_views.value(id); }
    int liveViews() const;
    static QVariantList buttonModel(bool right, const QString &title);

public slots:
    void apply();
    void openMenu(int id);
    void closeMenu();

private:
    QScreen *screenFor(const QString &output) const;
    QQuickView *make(const Strip &s);
    void place(QQuickView *v, const Strip &s, QScreen *screen);

    FrameController *m_c;
    QQmlEngine *m_engine;
    QHash<int, QQuickView *> m_views;
    QPointer<QQuickView> m_menu;
};
