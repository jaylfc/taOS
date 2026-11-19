#pragma once
#include <QObject>

#include "audiotypes.h"

// What the audio controller needs from a sound server. PulseBackend is the
// real one (libpulse, which PipeWire serves through pipewire-pulse); the tests
// also use a recording fake.
class AudioBackend : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    virtual const AudioState &state() const = 0;
    // Make `name` the default and move the streams that are already playing (or recording) to it.
    virtual void setDefault(bool sink, const QString &name) = 0;
    virtual void setVolume(bool sink, quint32 index, int percent) = 0;
    virtual void setMute(bool sink, quint32 index, bool mute) = 0;
    virtual void setPort(bool sink, quint32 index, const QString &port) = 0;
    virtual void setCardProfile(quint32 card, const QString &profile) = 0;
    // Live input level, only while asked for (the menu is open).
    virtual void startMeter(const QString &sourceName) = 0;
    virtual void stopMeter() = 0;
signals:
    void stateChanged();
    void failed(const QString &what);  // something the person asked for did not work
    void meterLevel(double level);     // 0..1
};
