// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>
#include <QVector>

#include "desktopentry.h"
#include "windowinfo.h"

// One window button of the taskbar.
struct TaskbarButton {
    int id = 0;               // the window id the engine uses
    QString appId;
    QString title;            // the window title; the app's name when the window has none
    QString name;             // the application's display name
    QString icon;             // icon name or path, "" when unknown
    bool focused = false;
    bool minimised = false;
    bool operator==(const TaskbarButton &o) const {
        return id == o.id && appId == o.appId && title == o.title && name == o.name && icon == o.icon
            && focused == o.focused && minimised == o.minimised;
    }
};

// What the taskbar shows, derived only from the compositor's window list. The order is the order the windows
// appeared in (oldest first) and never changes with focus, so a button stays where the hand expects it. A closed window
// leaves a gap that closes up; a new window goes to the end. Minimised windows keep their place.
class TaskbarModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY changed)
    Q_PROPERTY(int focusedPosition READ focusedPosition NOTIFY changed)
public:
    enum Role { IdRole = Qt::UserRole + 1, TitleRole, NameRole, IconRole, FocusedRole, MinimisedRole, AccessibleRole, TooltipRole };
    explicit TaskbarModel(QObject *parent = nullptr);

    void setIndex(const DesktopIndex *idx) { m_idx = idx; }
    void setWindows(const QVector<WindowInfo> &w);
    const QVector<TaskbarButton> &buttons() const { return m_buttons; }
    int focusedPosition() const;                 // -1 when none has focus
    int positionOf(int id) const;

    int rowCount(const QModelIndex &p = QModelIndex()) const override { return p.isValid() ? 0 : m_buttons.size(); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
signals:
    void changed();

private:
    const DesktopIndex *m_idx = nullptr;
    QVector<int> m_order;                        // window ids, oldest first
    QVector<TaskbarButton> m_buttons;
};

// How the buttons share the width the taskbar has. Pure arithmetic, tested on its own.
namespace TaskbarLayout {
struct Result {
    int buttonWidth = 0;      // every visible button has this width
    int first = 0;            // position of the first visible button
    int visible = 0;          // how many buttons are drawn
    int hidden = 0;           // how many are in the overflow menu (the "more" button is drawn when > 0)
};
// `available`: the width for the buttons and the overflow button (the start button and the tray are already taken off).
// All buttons share the width up to `maxW`; when even `minW` does not fit, as many as fit are drawn, with room for
// the overflow button of `moreW`, and the window that has focus (`focused`, -1 for none) is always among them.
Result compute(int available, int count, int focused, int minW, int maxW, int gap, int moreW);
}
