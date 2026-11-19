#include "menuhost.h"

#include <QAccessible>
#include <QGuiApplication>
#include <QQuickItem>
#include <QScreen>

#include "bluezclient.h"
#include "network.h"
#include "nmclient.h"

MenuHost::MenuHost(NmClient *nm, BluezClient *bt, QObject *parent) : QObject(parent), m_nm(nm), m_bt(bt) {
    // A pairing request that arrives while the menu is closed opens it, top right of the main screen.
    connect(bt, &BluezClient::attention, this, [this] {
        if (QScreen *s = QGuiApplication::primaryScreen()) emit openRequested(s->name(), s->geometry().width() - 60);
    });
}

void MenuHost::setOpen(bool on) {
    if (m_open == on) return;
    m_open = on;
    m_nm->setActive(on);
    m_bt->setActive(on);
    emit openChanged();
}

void MenuHost::openNetwork(const QString &output, qreal anchorRight) { emit openRequested(output, anchorRight); }
void MenuHost::finishClose() { emit closeFinished(); }

void MenuHost::announce(QQuickItem *item, const QString &text) {
    if (!item || text.isEmpty() || !QAccessible::isActive()) return;
    QAccessibleAnnouncementEvent ev(item, text);
    ev.setPoliteness(QAccessible::AnnouncementPoliteness::Polite);
    QAccessible::updateAccessibility(&ev);
}
