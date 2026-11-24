// SPDX-License-Identifier: AGPL-3.0-or-later
#include "menuhost.h"

#include <QAccessible>
#include <QGuiApplication>
#include <QQuickItem>
#include <QScreen>

#include "audio.h"
#include "bluezclient.h"
#include "network.h"
#include "nmclient.h"

MenuHost::MenuHost(NmClient *nm, BluezClient *bt, Audio *audio, QObject *parent)
    : QObject(parent), m_nm(nm), m_bt(bt), m_audio(audio) {
    // A pairing request that arrives while the menu is closed opens it, top right of the main screen.
    connect(bt, &BluezClient::attention, this, [this] {
        if (QScreen *s = QGuiApplication::primaryScreen()) emit openRequested(QStringLiteral("network"), s->name(), s->geometry().width() - 60);
    });
}

void MenuHost::setOpen(bool on, const QString &kind) {
    if (on) {
        if (m_open && m_kind != kind) setOpen(false, m_kind);
        m_kind = kind;
    } else if (!m_open) {
        return;
    }
    m_open = on;
    if (m_kind == QLatin1String("audio")) {
        m_audio->setMenuOpen(on);
    } else if (m_kind == QLatin1String("display")) {
        // Nothing to switch to live: the display controller reads when the menu asks.
    } else {
        m_nm->setActive(on);
        m_bt->setActive(on);
    }
    emit openChanged();
}

void MenuHost::openNetwork(const QString &output, qreal anchorRight) { emit openRequested(QStringLiteral("network"), output, anchorRight); }
void MenuHost::openAudio(const QString &output, qreal anchorRight) { emit openRequested(QStringLiteral("audio"), output, anchorRight); }
void MenuHost::openDisplay(const QString &output, qreal anchorRight) { emit openRequested(QStringLiteral("display"), output, anchorRight); }
void MenuHost::finishClose() { emit closeFinished(); }

void MenuHost::announce(QQuickItem *item, const QString &text) {
    if (!item || text.isEmpty() || !QAccessible::isActive()) return;
    QAccessibleAnnouncementEvent ev(item, text);
    ev.setPoliteness(QAccessible::AnnouncementPoliteness::Polite);
    QAccessible::updateAccessibility(&ev);
}
