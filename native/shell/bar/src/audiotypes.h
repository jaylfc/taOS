#pragma once
#include <QList>
#include <QString>
#include <QtGlobal>

// Plain data describing what the sound server reports. No libpulse types, so the
// naming, filtering and profile logic can be tested with hand written data.
constexpr quint32 kNoIndex = 0xFFFFFFFFu;

enum class Avail { Unknown, No, Yes };

struct AudioPort {
    QString name, description, type;
    int priority = 0;
    Avail available = Avail::Unknown;
};

struct AudioProfile {
    QString name, description;
    int nSinks = 0, nSources = 0;
    int priority = 0;
    bool available = true;
};

struct AudioCard {
    quint32 index = kNoIndex;
    QString name, description, bus, formFactor, activeProfile, vendor, product;
    bool bluetooth = false;
    QList<AudioProfile> profiles;
};

struct AudioDevice {
    bool isSink = true;
    quint32 index = kNoIndex;
    quint32 card = kNoIndex;
    QString name, description, activePort, bus, formFactor, product;
    int volume = 0;  // percent of the 100% mark, the loudest channel
    bool mute = false;
    bool monitor = false;  // a source that is just the monitor of a sink
    QList<AudioPort> ports;
};

struct AudioStream {
    quint32 index = kNoIndex;
    quint32 device = kNoIndex;  // sink index
    QString name;
};

struct AudioState {
    bool connected = false;
    QString defaultSink, defaultSource;
    QList<AudioDevice> sinks, sources;
    QList<AudioCard> cards;
    QList<AudioStream> streams;

    const AudioDevice *defaultSinkDevice() const {
        for (const AudioDevice &d : sinks) if (d.name == defaultSink) return &d;
        return nullptr;
    }
    const AudioDevice *defaultSourceDevice() const {
        for (const AudioDevice &d : sources) if (d.name == defaultSource) return &d;
        return nullptr;
    }
    const AudioCard *card(quint32 index) const {
        for (const AudioCard &c : cards) if (c.index == index) return &c;
        return nullptr;
    }
};

// One selectable row of the output or input list.
struct AudioChoice {
    QString id;           // "dev:<name>:<port>" or "profile:<card>:<profile>"
    bool output = true;
    QString title, subtitle, icon;  // icon: usb, bluetooth, hdmi, analog, speaker, headphones, mic
    bool active = false;
    QString deviceName, portName;
    quint32 card = kNoIndex;
    QString profileName;  // set when choosing it means switching the card profile first
    int rank = 0;
};

// One option of the per-device profile picker.
struct ProfileChoice {
    quint32 card = kNoIndex;
    QString name, label;
    bool active = false;
};

namespace AudioNames {
QList<AudioChoice> choices(const AudioState &s, bool output);
// Picker for the card behind the default output (or input): Bluetooth music vs headset, analog vs HDMI.
QList<ProfileChoice> profileChoices(const AudioState &s, bool output, QString *cardTitle = nullptr);
QString cleanDescription(const QString &d);
}  // namespace AudioNames
