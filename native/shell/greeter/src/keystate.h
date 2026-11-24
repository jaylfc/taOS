// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>

// KeyState: Caps Lock. Qt has no API for it, so this watches key events and
// reads the xkb modifier mask the Wayland platform plugin attaches to them
// (LockMask is bit 1). The state is unknown until the first key press.
class KeyState : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool capsLock READ capsLock NOTIFY capsLockChanged)
    Q_PROPERTY(bool keyboardUsed READ keyboardUsed NOTIFY keyboardUsedChanged)
public:
    explicit KeyState(QObject *parent = nullptr) : QObject(parent) {}
    bool capsLock() const { return m_caps; }
    // True after the first key press: focus rings only show for keyboard users.
    bool keyboardUsed() const { return m_used; }
    bool eventFilter(QObject *o, QEvent *e) override;
signals:
    void capsLockChanged();
    void keyboardUsedChanged();
private:
    bool m_caps = false;
    bool m_used = false;
};
