// SPDX-License-Identifier: AGPL-3.0-or-later
#include "displaymodel.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtMath>
#include <algorithm>

QString DisplayOutput::key() const {
    auto or_unknown = [](const QString &s) { return s.trimmed().isEmpty() ? QStringLiteral("Unknown") : s; };
    return or_unknown(make) + QLatin1Char(' ') + or_unknown(model) + QLatin1Char(' ') + or_unknown(serial);
}

QString DisplayOutput::label() const {
    const QString m = model.trimmed();
    if (!m.isEmpty() && m != QLatin1String("Unknown")) return m;
    return name;
}

QString hzText(int mHz) {
    const double hz = mHz / 1000.0;
    if (qAbs(hz - qRound(hz)) < 0.01) return QString::number(qRound(hz));
    QString s = QString::number(hz, 'f', 2);
    while (s.endsWith(QLatin1Char('0'))) s.chop(1);
    if (s.endsWith(QLatin1Char('.'))) s.chop(1);
    return s;
}

QString modeText(const DisplayMode &m) {
    return QStringLiteral("%1 x %2, %3 Hz").arg(m.width).arg(m.height).arg(hzText(m.mHz));
}

QString swayModeArg(int w, int h, int mHz) {
    // Millihertz precision: 59.94 must stay 59.94, not become 60.
    QString hz = QString::number(mHz / 1000.0, 'f', 3);
    while (hz.endsWith(QLatin1Char('0'))) hz.chop(1);
    if (hz.endsWith(QLatin1Char('.'))) hz.chop(1);
    return QStringLiteral("%1x%2@%3Hz").arg(w).arg(h).arg(hz);
}

static DisplayMode modeFromJson(const QJsonObject &o) {
    DisplayMode m;
    m.width = o.value(QLatin1String("width")).toInt();
    m.height = o.value(QLatin1String("height")).toInt();
    m.mHz = o.value(QLatin1String("refresh")).toInt();
    m.preferred = o.value(QLatin1String("preferred")).toBool(false);
    return m;
}

bool parseOutputs(const QByteArray &json, QList<DisplayOutput> *out, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isArray()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not an array");
        return false;
    }
    out->clear();
    for (const QJsonValue &v : d.array()) {
        const QJsonObject o = v.toObject();
        if (!o.value(QLatin1String("active")).toBool(true)) continue;
        DisplayOutput r;
        r.name = o.value(QLatin1String("name")).toString();
        r.make = o.value(QLatin1String("make")).toString();
        r.model = o.value(QLatin1String("model")).toString();
        r.serial = o.value(QLatin1String("serial")).toString();
        r.active = true;
        r.scale = o.value(QLatin1String("scale")).toDouble(1.0);
        r.current = modeFromJson(o.value(QLatin1String("current_mode")).toObject());
        for (const QJsonValue &mv : o.value(QLatin1String("modes")).toArray()) {
            const DisplayMode m = modeFromJson(mv.toObject());
            if (m.valid()) r.modes << m;
        }
        // A "physical" or "preferred_mode" extension, if a future compositor adds one.
        const QJsonObject ph = o.value(QLatin1String("physical_size")).toObject();
        if (!ph.isEmpty()) r.physicalMm = QSizeF(ph.value(QLatin1String("width")).toDouble(), ph.value(QLatin1String("height")).toDouble());
        if (!r.name.isEmpty()) *out << r;
    }
    return true;
}

static bool treeWalk(const QJsonObject &n) {
    if (n.value(QLatin1String("shell")).toString() == QLatin1String("xwayland")) return true;
    for (const char *k : {"nodes", "floating_nodes"})
        for (const QJsonValue &c : n.value(QLatin1String(k)).toArray())
            if (treeWalk(c.toObject())) return true;
    return false;
}

bool treeHasXwayland(const QByteArray &json) {
    const QJsonDocument d = QJsonDocument::fromJson(json);
    return d.isObject() && treeWalk(d.object());
}

QList<int> ratesAt(const DisplayOutput &o, const QSize &res) {
    QList<int> r;
    for (const DisplayMode &m : o.modes)
        if (m.width == res.width() && m.height == res.height() && m.mHz > 0 && !r.contains(m.mHz)) r << m.mHz;
    std::sort(r.begin(), r.end(), std::greater<int>());
    return r;
}

NativeChoice chooseNative(const DisplayOutput &o, const QList<DisplayMode> &rejected) {
    NativeChoice c;
    // 1. The compositor's own preferred flag, when it provides one.
    for (const DisplayMode &m : o.modes)
        if (m.preferred) { c.resolution = QSize(m.width, m.height); c.source = NativeChoice::Flag; break; }
    // 2. The EDID's first listed mode (the kernel sorts the preferred one first),
    //    accepted only if the compositor really offers that resolution.
    if (c.source == NativeChoice::None && o.drmPreferred.isValid()) {
        for (const DisplayMode &m : o.modes)
            if (m.width == o.drmPreferred.width() && m.height == o.drmPreferred.height()) {
                c.resolution = o.drmPreferred;
                c.source = NativeChoice::Edid;
                break;
            }
    }
    // 3. Nothing known: keep the resolution that is running. Never guess upward.
    if (c.source == NativeChoice::None && o.current.width > 0 && o.current.height > 0) {
        c.resolution = QSize(o.current.width, o.current.height);
        c.source = NativeChoice::Current;
    }
    if (!c.resolution.isValid()) return c;
    DisplayMode best;
    for (const DisplayMode &m : o.modes) {
        if (m.width != c.resolution.width() || m.height != c.resolution.height()) continue;
        if (rejected.contains(m)) continue;
        if (m.mHz > best.mHz) best = m;
    }
    if (best.valid()) c.mode = best;
    else c.source = NativeChoice::None;
    return c;
}

QList<double> scalePresets() { return {1.0, 1.25, 1.5, 1.75, 2.0}; }

int scalePercent(double scale) { return qRound(scale * 100.0); }

double recommendedScale(int pxW, int pxH, double mmW, double mmH) {
    if (pxW <= 0 || pxH <= 0 || mmW <= 0 || mmH <= 0) return 1.0;
    // The EDID's "aspect ratio only" placeholders and zero-ish sizes.
    if ((qRound(mmW) == 160 && (qRound(mmH) == 90 || qRound(mmH) == 100)) || (qRound(mmW) == 40 && qRound(mmH) == 30)) return 1.0;
    const double dpiX = pxW / (mmW / 25.4), dpiY = pxH / (mmH / 25.4);
    if (dpiX < 40 || dpiX > 500 || dpiY < 40 || dpiY > 500) return 1.0;
    // Physical and pixel aspect ratios must agree, or the size is not trusted.
    const double ar = (double(pxW) / pxH) / (mmW / mmH);
    if (ar < 0.75 || ar > 1.33) return 1.0;
    const double ideal = dpiX / 125.0;
    double best = 1.0, bestDiff = 1e9;
    for (double p : scalePresets()) {
        const double d = qAbs(p - ideal);
        if (d < bestDiff - 1e-9) { best = p; bestDiff = d; }
    }
    return best;
}

double recommendedScale(const DisplayOutput &o, const QSize &res) {
    return recommendedScale(res.width(), res.height(), o.physicalMm.width(), o.physicalMm.height());
}

QSizeF edidPhysicalMm(const QByteArray &e) {
    if (e.size() < 128) return {};
    auto b = [&](int i) { return int(uchar(e.at(i))); };
    // The first detailed timing descriptor carries the image size in mm.
    if (b(54) != 0 || b(55) != 0) {
        const int h = b(66) | ((b(68) & 0xF0) << 4);
        const int v = b(67) | ((b(68) & 0x0F) << 8);
        if (h > 0 && v > 0) return QSizeF(h, v);
    }
    // Else the basic block's size in cm (zero: projector or unknown).
    if (b(21) > 0 && b(22) > 0) return QSizeF(b(21) * 10.0, b(22) * 10.0);
    return {};
}

DrmHints readDrmHints(const QString &connector, const QString &sysRoot) {
    DrmHints h;
    // card0-HDMI-A-1, card1-eDP-1 ...
    const QStringList dirs = QDir(sysRoot).entryList({QStringLiteral("card*-") + connector}, QDir::Dirs | QDir::NoDotAndDotDot);
    for (const QString &d : dirs) {
        const QString base = sysRoot + QLatin1Char('/') + d;
        QFile modes(base + QStringLiteral("/modes"));
        if (!h.preferred.isValid() && modes.open(QIODevice::ReadOnly)) {
            const QString first = QString::fromLatin1(modes.readLine()).trimmed();
            const QStringList p = first.split(QLatin1Char('x'));
            if (p.size() == 2) {
                bool a = false, b = false;
                const int w = p[0].toInt(&a), hh = p[1].toInt(&b);  // "1920x1080i" fails on purpose: interlaced is not native
                if (a && b) h.preferred = QSize(w, hh);
            }
        }
        QFile edid(base + QStringLiteral("/edid"));
        if (h.physicalMm.isEmpty() && edid.open(QIODevice::ReadOnly)) h.physicalMm = edidPhysicalMm(edid.readAll());
    }
    return h;
}
