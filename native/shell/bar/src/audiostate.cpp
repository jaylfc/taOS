#include "audiotypes.h"

#include <QCoreApplication>
#include <QRegularExpression>
#include <QHash>
#include <QSet>
#include <algorithm>

namespace AudioNames {

static QString tr(const char *s) { return QCoreApplication::translate("AudioNames", s); }

QString cleanDescription(const QString &d) {
    static const QRegularExpression tail(
        QStringLiteral(R"((\s+(Analog|Digital|Stereo|Mono|Surround\s*[0-9.]+|Duplex|Pro|Output|Input|Speakers|\(IEC958\)|\(HDMI\)|\(DisplayPort\)))+\s*$)"),
        QRegularExpression::CaseInsensitiveOption);
    QString r = d;
    r.remove(tail);
    return r.trimmed().isEmpty() ? d.trimmed() : r.trimmed();
}

static bool has(const QString &s, const char *needle) { return s.contains(QLatin1String(needle), Qt::CaseInsensitive); }

// "output:hdmi-stereo-extra2" -> its output parts {"hdmi-stereo-extra2"}
static QStringList parts(const QString &profile, const QString &prefix) {
    QStringList out;
    for (const QString &p : profile.split(QLatin1Char('+')))
        if (p.startsWith(prefix)) out << p.mid(prefix.size());
    std::sort(out.begin(), out.end());
    return out;
}

static QString jackWord(const QString &s) {
    if (has(s, "front")) return tr("front jack");
    if (has(s, "rear") || has(s, "lineout") || has(s, "line-out") || has(s, "line out")) return tr("rear jack");
    return tr("analog jack");
}

static QString hdmiLabel(const QString &text) {
    // "HDMI / DisplayPort 3" -> "HDMI 3"; "DisplayPort" alone stays DisplayPort.
    const bool dpOnly = has(text, "displayport") && !has(text, "hdmi");
    static const QRegularExpression num(QStringLiteral(R"((\d+)\s*$)"));
    static const QRegularExpression extra(QStringLiteral(R"(extra(\d+))"));
    QString n;
    const auto m = extra.match(text);
    if (m.hasMatch()) n = QString::number(m.captured(1).toInt() + 1);
    else if (const auto k = num.match(text); k.hasMatch()) n = k.captured(1);
    return (dpOnly ? QStringLiteral("DisplayPort") : QStringLiteral("HDMI")) + (n.isEmpty() ? QString() : QStringLiteral(" ") + n);
}

static QString monitorName(const QString &portDesc) {
    const int a = portDesc.indexOf(QLatin1Char('(')), b = portDesc.lastIndexOf(QLatin1Char(')'));
    if (a >= 0 && b > a) return portDesc.mid(a + 1, b - a - 1).trimmed();
    return {};
}

struct Naming {
    QString title, subtitle, icon;
    int rank = 9;
};

static Naming nameFor(const AudioDevice &d, const AudioPort *p, const AudioCard *card, bool output) {
    Naming n;
    const QString portName = p ? p->name : QString();
    const QString portDesc = p ? p->description : QString();
    const QString clean = cleanDescription(d.description);
    const QString ff = d.formFactor;
    const bool bt = d.bus == QLatin1String("bluetooth") || (card && card->bluetooth);
    const bool usb = d.bus == QLatin1String("usb") || (card && card->bus == QLatin1String("usb"));
    const bool hdmi = has(portName, "hdmi") || has(portName, "displayport") || (p && p->type == QLatin1String("hdmi"))
                      || (!p && (has(d.name, "hdmi") || has(d.name, "displayport")));
    if (bt) {
        const QString alias = card && !card->description.isEmpty() ? card->description : clean;
        n.icon = QStringLiteral("bluetooth");
        n.rank = 0;
        n.subtitle = QCoreApplication::translate("AudioNames", "Bluetooth");
        if (output) {
            if (ff == QLatin1String("speaker") || ff == QLatin1String("portable") || ff == QLatin1String("hifi"))
                n.title = tr("Speaker (%1)").arg(alias);
            else if (ff.isEmpty() || ff == QLatin1String("headset") || ff == QLatin1String("headphone") || ff == QLatin1String("hands-free"))
                n.title = tr("Headphones (%1)").arg(alias);
            else
                n.title = alias;
        } else {
            n.title = tr("Headset microphone (%1)").arg(alias);
        }
    } else if (usb) {
        n.icon = QStringLiteral("usb");
        n.rank = 1;
        n.subtitle = d.product.isEmpty() ? clean : d.product;
        if (ff == QLatin1String("headset")) n.title = tr("USB headset");
        else if (ff == QLatin1String("headphone")) n.title = tr("USB headphones");
        else if (ff == QLatin1String("speaker")) n.title = tr("USB speakers");
        else if (ff == QLatin1String("microphone") || !output) n.title = tr("USB microphone");
        else n.title = tr("USB audio");
    } else if (hdmi) {
        n.icon = QStringLiteral("hdmi");
        n.rank = 2;
        const QString mon = monitorName(portDesc);
        n.title = tr("Monitor speakers (%1)").arg(hdmiLabel(portDesc.isEmpty() ? portName + QLatin1Char(' ') + d.name : portDesc));
        n.subtitle = mon.isEmpty() ? (card ? cleanDescription(card->description) : clean) : mon;
    } else if (has(portName, "headphone") || ff == QLatin1String("headphone")) {
        n.icon = QStringLiteral("headphones");
        n.rank = 3;
        n.title = tr("Headphones (%1)").arg(jackWord(portName + portDesc));
        n.subtitle = card ? cleanDescription(card->description) : clean;
    } else if (has(portName, "headset") || ff == QLatin1String("headset")) {
        n.icon = QStringLiteral("headphones");
        n.rank = 3;
        n.title = output ? tr("Headset (%1)").arg(jackWord(portName + portDesc)) : tr("Headset microphone (%1)").arg(jackWord(portName + portDesc));
        n.subtitle = card ? cleanDescription(card->description) : clean;
    } else if (has(portName, "lineout") || has(portName, "line-out")) {
        n.icon = QStringLiteral("analog");
        n.rank = 4;
        n.title = tr("Line out (%1)").arg(jackWord(portName + portDesc));
        n.subtitle = card ? cleanDescription(card->description) : clean;
    } else if (has(portName, "speaker") || ff == QLatin1String("speaker") || ff == QLatin1String("internal")) {
        n.icon = QStringLiteral("speaker");
        n.rank = 4;
        const QString j = jackWord(portName + portDesc);
        n.title = has(portName + portDesc, "rear") ? tr("Speakers (%1)").arg(j) : tr("Speakers");
        n.subtitle = card ? cleanDescription(card->description) : clean;
    } else if (!output && (has(portName, "mic") || has(portName, "line") || ff == QLatin1String("microphone"))) {
        n.icon = QStringLiteral("mic");
        n.rank = 4;
        n.title = has(portName, "linein") || has(portName, "line-in") ? tr("Line in (%1)").arg(jackWord(portName + portDesc))
                                                                       : tr("Microphone (%1)").arg(jackWord(portName + portDesc));
        n.subtitle = card ? cleanDescription(card->description) : clean;
    } else {
        n.icon = output ? QStringLiteral("speaker") : QStringLiteral("mic");
        n.rank = 5;
        n.title = !portDesc.isEmpty() && card ? portDesc : clean;
        n.subtitle = card ? cleanDescription(card->description) : QString();
        if (n.subtitle == n.title) n.subtitle.clear();
    }
    return n;
}

static Naming nameForProfile(const AudioCard &c, const AudioProfile &prof, bool output) {
    const QString part = parts(prof.name, output ? QStringLiteral("output:") : QStringLiteral("input:")).value(0);
    AudioDevice d;
    d.description = c.description;
    d.bus = c.bus;
    d.formFactor = c.formFactor;
    AudioPort p;
    p.name = part;
    p.description = part;  // the profile's own wording would put its parentheses into the title
    if (has(part, "hdmi") || has(part, "displayport")) p.type = QStringLiteral("hdmi");
    else if (has(part, "headphone")) p.name = QStringLiteral("headphones");
    else if (has(part, "speaker")) p.name = QStringLiteral("speaker");
    else if (has(part, "analog")) p.name = output ? QStringLiteral("analog-output-speaker") : QStringLiteral("analog-input-mic");
    Naming n = nameFor(d, &p, &c, output);
    if (has(part, "analog") && !c.bluetooth && !(c.bus == QLatin1String("usb"))) {
        n.title = output ? tr("Speakers (analog jack)") : tr("Microphone (analog jack)");
        n.icon = output ? QStringLiteral("speaker") : QStringLiteral("mic");
        n.rank = 4;
    }
    return n;
}

QList<AudioChoice> choices(const AudioState &s, bool output) {
    QList<AudioChoice> out;
    const QList<AudioDevice> &devs = output ? s.sinks : s.sources;
    const QString def = output ? s.defaultSink : s.defaultSource;
    for (const AudioDevice &d : devs) {
        if (d.monitor) continue;
        const AudioCard *card = s.card(d.card);
        // Ports that are not plugged in are not offered. A device without ports is offered as it is.
        QList<const AudioPort *> usable;
        for (const AudioPort &p : d.ports)
            if (p.available != Avail::No) usable << &p;
        if (d.ports.isEmpty()) usable << nullptr;
        for (const AudioPort *p : usable) {
            Naming n = nameFor(d, p, card, output);
            AudioChoice c;
            c.id = QStringLiteral("dev:%1:%2").arg(d.name, p ? p->name : QString());
            c.output = output;
            c.title = n.title; c.subtitle = n.subtitle; c.icon = n.icon; c.rank = n.rank;
            c.deviceName = d.name;
            c.portName = p ? p->name : QString();
            c.card = d.card;
            c.active = d.name == def && (!p || d.activePort.isEmpty() || d.activePort == p->name);
            out << c;
        }
    }
    // Outputs that live on another profile of a card (analog vs HDMI on one chip): offered when available.
    QSet<QString> seenTitles;
    for (const AudioChoice &c : out) seenTitles.insert(c.title + QLatin1Char('|') + c.subtitle);
    const QString prefix = output ? QStringLiteral("output:") : QStringLiteral("input:");
    for (const AudioCard &c : s.cards) {
        if (c.bluetooth) continue;  // music vs headset is the profile picker's job
        const QStringList activeParts = parts(c.activeProfile, prefix);
        QHash<QString, const AudioProfile *> best;
        for (const AudioProfile &p : c.profiles) {
            if (!p.available || p.name == QLatin1String("off") || has(p.name, "pro-audio")) continue;
            if ((output ? p.nSinks : p.nSources) <= 0) continue;
            const QStringList ps = parts(p.name, prefix);
            if (ps.isEmpty() || ps == activeParts) continue;
            const QString key = ps.join(QLatin1Char('+'));
            if (!best.contains(key) || best.value(key)->priority < p.priority) best.insert(key, &p);
        }
        for (auto it = best.cbegin(); it != best.cend(); ++it) {
            Naming n = nameForProfile(c, *it.value(), output);
            if (seenTitles.contains(n.title + QLatin1Char('|') + n.subtitle)) continue;
            AudioChoice ch;
            ch.id = QStringLiteral("profile:%1:%2").arg(c.index).arg(it.value()->name);
            ch.output = output;
            ch.title = n.title; ch.subtitle = n.subtitle; ch.icon = n.icon; ch.rank = n.rank;
            ch.card = c.index;
            ch.profileName = it.value()->name;
            out << ch;
        }
    }
    std::sort(out.begin(), out.end(), [](const AudioChoice &a, const AudioChoice &b) {
        if (a.rank != b.rank) return a.rank < b.rank;
        const int t = a.title.compare(b.title, Qt::CaseInsensitive);
        if (t != 0) return t < 0;
        return a.id < b.id;
    });
    return out;
}

QList<ProfileChoice> profileChoices(const AudioState &s, bool output, QString *cardTitle) {
    QList<ProfileChoice> out;
    const AudioDevice *d = output ? s.defaultSinkDevice() : s.defaultSourceDevice();
    if (!d) return out;
    const AudioCard *c = s.card(d->card);
    if (!c) return out;
    if (cardTitle) *cardTitle = c->bluetooth ? nameFor(*d, nullptr, c, output).title : cleanDescription(c->description);
    if (c->bluetooth) {
        // Two real choices on Bluetooth: music quality or a microphone.
        const AudioProfile *music = nullptr, *headset = nullptr;
        for (const AudioProfile &p : c->profiles) {
            if (!p.available) continue;
            if (p.name.startsWith(QLatin1String("a2dp"))) { if (!music || p.priority > music->priority) music = &p; }
            else if (p.name.startsWith(QLatin1String("headset-head-unit")) || p.name.startsWith(QLatin1String("hsp")) || p.name.startsWith(QLatin1String("hfp"))) {
                if (!headset || p.priority > headset->priority) headset = &p;
            }
        }
        if (music && headset) {
            auto isHeadset = [&](const QString &n) { return n.startsWith(QLatin1String("headset-head-unit")) || n.startsWith(QLatin1String("hsp")) || n.startsWith(QLatin1String("hfp")); };
            out << ProfileChoice{c->index, music->name, tr("High quality music"), c->activeProfile.startsWith(QLatin1String("a2dp"))}
                << ProfileChoice{c->index, headset->name, tr("Headset with microphone"), isHeadset(c->activeProfile)};
        }
        return out;
    }
    QList<const AudioProfile *> usable;
    for (const AudioProfile &p : c->profiles) {
        if (!p.available || p.name == QLatin1String("off") || has(p.name, "pro-audio")) continue;
        if (p.nSinks + p.nSources <= 0) continue;
        usable << &p;
    }
    if (usable.size() < 2) return out;
    for (const AudioProfile *p : usable) out << ProfileChoice{c->index, p->name, p->description, p->name == c->activeProfile};
    return out;
}

}  // namespace AudioNames
