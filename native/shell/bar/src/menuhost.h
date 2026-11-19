#pragma once
#include <QObject>

class QQuickItem;
class NmClient;
class BluezClient;

// The bridge between the bar items and the network menu window: says where
// the menu should open, switches the backends to "live" while it is open
// (scan, discovery, pairing agent) and speaks state changes to assistive tech.
class MenuHost : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool open READ isOpen NOTIFY openChanged)
public:
    MenuHost(NmClient *nm, BluezClient *bt, QObject *parent = nullptr);
    bool isOpen() const { return m_open; }
    void setOpen(bool on);

    // From the bar: open the network menu on `output`, its right edge under `anchorRight` (px).
    Q_INVOKABLE void openNetwork(const QString &output, qreal anchorRight);
    // From the menu, after its closing animation.
    Q_INVOKABLE void finishClose();
    // Announce a state change to screen readers, politely.
    Q_INVOKABLE void announce(QQuickItem *item, const QString &text);

signals:
    void openChanged();
    void openRequested(const QString &output, qreal anchorRight);
    void closeFinished();

private:
    NmClient *m_nm;
    BluezClient *m_bt;
    bool m_open = false;
};
