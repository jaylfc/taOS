#pragma once
#include <QObject>

class QQuickItem;
class NmClient;
class BluezClient;
class Audio;

// The bridge between the bar items and the network menu window: says where
// the menu should open, switches the backends to "live" while it is open
// (scan, discovery, pairing agent) and speaks state changes to assistive tech.
class MenuHost : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool open READ isOpen NOTIFY openChanged)
public:
    MenuHost(NmClient *nm, BluezClient *bt, Audio *audio, QObject *parent = nullptr);
    bool isOpen() const { return m_open; }
    // Switches the backends of that menu to live (or back). One menu at a time.
    void setOpen(bool on, const QString &kind);

    // From the bar: open the network menu on `output`, its right edge under `anchorRight` (px).
    Q_INVOKABLE void openNetwork(const QString &output, qreal anchorRight);
    Q_INVOKABLE void openAudio(const QString &output, qreal anchorRight);
    Q_INVOKABLE void openDisplay(const QString &output, qreal anchorRight);
    // From the menu, after its closing animation.
    Q_INVOKABLE void finishClose();
    // Announce a state change to screen readers, politely.
    Q_INVOKABLE void announce(QQuickItem *item, const QString &text);

signals:
    void openChanged();
    // kind: "network", "audio" or "display"
    void openRequested(const QString &kind, const QString &output, qreal anchorRight);
    void closeFinished();

private:
    NmClient *m_nm;
    BluezClient *m_bt;
    Audio *m_audio;
    bool m_open = false;
    QString m_kind;
};
