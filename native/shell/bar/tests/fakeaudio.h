#pragma once
#include "audiobackend.h"

// Records what the controller asks for; the test plays the server.
class FakeAudioBackend : public AudioBackend {
    Q_OBJECT
public:
    AudioState st;
    QStringList calls;
    int meters = 0;
    const AudioState &state() const override { return st; }
    void setDefault(bool sink, const QString &name) override {
        calls << QStringLiteral("default %1 %2").arg(sink ? "sink" : "source", name);
        (sink ? st.defaultSink : st.defaultSource) = name;
        emit stateChanged();
    }
    void setVolume(bool sink, quint32 i, int p) override {
        calls << QStringLiteral("volume %1 %2 %3").arg(sink ? "sink" : "source").arg(i).arg(p);
        for (AudioDevice &d : sink ? st.sinks : st.sources) if (d.index == i) d.volume = p;
        emit stateChanged();
    }
    void setMute(bool sink, quint32 i, bool m) override {
        calls << QStringLiteral("mute %1 %2 %3").arg(sink ? "sink" : "source").arg(i).arg(m);
        for (AudioDevice &d : sink ? st.sinks : st.sources) if (d.index == i) d.mute = m;
        emit stateChanged();
    }
    void setPort(bool sink, quint32 i, const QString &port) override {
        calls << QStringLiteral("port %1 %2 %3").arg(sink ? "sink" : "source").arg(i).arg(port);
        for (AudioDevice &d : sink ? st.sinks : st.sources) if (d.index == i) d.activePort = port;
    }
    void setCardProfile(quint32 card, const QString &profile) override { calls << QStringLiteral("profile %1 %2").arg(card).arg(profile); }
    void startMeter(const QString &src) override { calls << "meter-start " + src; meters = 1; }
    void stopMeter() override { calls << QStringLiteral("meter-stop"); meters = 0; }
};

namespace Fx {
inline AudioDevice sink(quint32 idx, const QString &name, const QString &desc, quint32 card = kNoIndex) {
    AudioDevice d; d.isSink = true; d.index = idx; d.name = name; d.description = desc; d.card = card; d.volume = 50; return d;
}
inline AudioDevice source(quint32 idx, const QString &name, const QString &desc, quint32 card = kNoIndex) {
    AudioDevice d; d.isSink = false; d.index = idx; d.name = name; d.description = desc; d.card = card; d.volume = 80; return d;
}
inline AudioProfile prof(const QString &n, const QString &d, int sinks, int sources, int prio = 1, bool avail = true) {
    AudioProfile p; p.name = n; p.description = d; p.nSinks = sinks; p.nSources = sources; p.priority = prio; p.available = avail; return p;
}
}  // namespace Fx
