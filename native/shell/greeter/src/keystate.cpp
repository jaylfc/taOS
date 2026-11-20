#include "keystate.h"

#include <QKeyEvent>

bool KeyState::eventFilter(QObject *o, QEvent *e) {
    if (e->type() == QEvent::KeyPress || e->type() == QEvent::KeyRelease) {
        auto *k = static_cast<QKeyEvent *>(e);
        if (e->type() == QEvent::KeyPress && !m_used) {
            m_used = true;
            emit keyboardUsedChanged();
        }
        const bool now = (k->nativeModifiers() & 0x2) != 0;  // xkb LockMask
        if (k->key() == Qt::Key_CapsLock) {
            // The modifier mask on the Caps key's own event may lag by one step.
            if (e->type() == QEvent::KeyRelease && now != m_caps) {
                m_caps = now;
                emit capsLockChanged();
            }
        } else if (now != m_caps) {
            m_caps = now;
            emit capsLockChanged();
        }
    }
    return QObject::eventFilter(o, e);
}
