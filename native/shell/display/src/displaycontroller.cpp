#include "displaycontroller.h"

#include <QDateTime>
#include <QThread>
#include <QVariantMap>
#include <QtMath>

static QString scaleArg(double s) {
    QString t = QString::number(s, 'f', 3);
    while (t.endsWith(QLatin1Char('0'))) t.chop(1);
    if (t.endsWith(QLatin1Char('.'))) t.chop(1);
    return t;
}

static bool sameMode(const DisplayMode &a, const DisplayMode &b) {
    return a.width == b.width && a.height == b.height && qAbs(a.mHz - b.mHz) <= 100;
}

DisplayController::DisplayController(SwayRunner *sway, DisplayConfigFile cfg, QString drmRoot, std::function<qint64()> nowMs, QObject *parent)
    : QObject(parent), m_sway(sway), m_cfg(std::move(cfg)), m_drmRoot(std::move(drmRoot)), m_now(std::move(nowMs)) {
    if (!m_now) m_now = [] { return QDateTime::currentMSecsSinceEpoch(); };
    m_sleep = [](int ms) { if (ms > 0) QThread::msleep(ms); };
    m_timer.setInterval(250);
    connect(&m_timer, &QTimer::timeout, this, &DisplayController::poll);
}

bool DisplayController::anyScaled() const {
    for (const DisplayOutput &o : m_outputs)
        if (o.scale > 1.001) return true;
    return false;
}

int DisplayController::trialSecondsLeft() const {
    if (!m_trial.active) return 0;
    const qint64 left = m_trial.deadline - m_now();
    return left <= 0 ? 0 : int((left + 999) / 1000);
}

void DisplayController::fail(const QString &text) {
    m_error = text;
    emit errorChanged();
}

void DisplayController::clearError() {
    if (m_error.isEmpty()) return;
    m_error.clear();
    emit errorChanged();
}

const DisplayOutput *DisplayController::find(const QString &name) const {
    for (const DisplayOutput &o : m_outputs)
        if (o.name == name) return &o;
    return nullptr;
}

bool DisplayController::readOutputs(QList<DisplayOutput> *out) {
    const SwayRunner::Result r = m_sway->run({QStringLiteral("-t"), QStringLiteral("get_outputs"), QStringLiteral("-r")});
    QString err = r.error;
    if (!r.ok || !parseOutputs(r.out, out, &err)) {
        fail(tr("Cannot read the displays: %1").arg(err));
        return false;
    }
    for (DisplayOutput &o : *out) {
        const DrmHints h = readDrmHints(o.name, m_drmRoot);
        o.drmPreferred = h.preferred;
        if (o.physicalMm.isEmpty()) o.physicalMm = h.physicalMm;
    }
    return true;
}

bool DisplayController::refresh() {
    QList<DisplayOutput> outs;
    if (!readOutputs(&outs)) return false;
    m_outputs = outs;
    const SwayRunner::Result t = m_sway->run({QStringLiteral("-t"), QStringLiteral("get_tree"), QStringLiteral("-r")});
    m_xwayland = t.ok && treeHasXwayland(t.out);
    rebuildModel();
    return true;
}

void DisplayController::rebuildModel() {
    QVariantList list;
    for (const DisplayOutput &o : m_outputs) {
        QVariantMap m;
        m[QStringLiteral("name")] = o.name;
        m[QStringLiteral("key")] = o.key();
        m[QStringLiteral("label")] = o.label();
        m[QStringLiteral("connector")] = o.name;
        m[QStringLiteral("modeText")] = o.current.valid() ? modeText(o.current) : QString();
        m[QStringLiteral("scale")] = o.scale;
        const int pct = scalePercent(o.scale);
        m[QStringLiteral("scalePercent")] = pct;
        QVariantList scales;
        bool custom = true;
        for (double p : scalePresets()) {
            scales << scalePercent(p);
            if (scalePercent(p) == pct) custom = false;
        }
        if (custom) {
            // The current value joins the presets in order.
            QVariantList withCustom;
            bool put = false;
            for (const QVariant &v : scales) {
                if (!put && v.toInt() > pct) { withCustom << pct; put = true; }
                withCustom << v;
            }
            if (!put) withCustom << pct;
            scales = withCustom;
        }
        m[QStringLiteral("scales")] = scales;
        const NativeChoice nc = chooseNative(o);
        QVariantList rates;
        if (nc.resolution.isValid())
            for (int hz : ratesAt(o, nc.resolution)) {
                QVariantMap r;
                r[QStringLiteral("mHz")] = hz;
                r[QStringLiteral("text")] = tr("%1 Hz").arg(hzText(hz));
                r[QStringLiteral("current")] = o.current.width == nc.resolution.width() && o.current.height == nc.resolution.height() && qAbs(o.current.mHz - hz) <= 100;
                rates << r;
            }
        m[QStringLiteral("rates")] = rates;
        m[QStringLiteral("nativeText")] = nc.resolution.isValid() ? QStringLiteral("%1 x %2").arg(nc.resolution.width()).arg(nc.resolution.height()) : QString();
        const double rec = recommendedScale(o, nc.resolution.isValid() ? nc.resolution : QSize(o.current.width, o.current.height));
        m[QStringLiteral("recommendedPercent")] = scalePercent(rec);
        m[QStringLiteral("recommendedText")] = nc.mode.valid() ? tr("%1, %2%").arg(modeText(nc.mode)).arg(scalePercent(rec)) : tr("%1%").arg(scalePercent(rec));
        m[QStringLiteral("isRecommended")] = nc.mode.valid() && sameMode(o.current, nc.mode) && scalePercent(rec) == pct;
        list << m;
    }
    m_model = list;
    emit outputsChanged();
}

bool DisplayController::setModeChecked(const QString &name, const DisplayMode &target, const DisplayMode &previous) {
    auto revertTo = [&] {
        if (previous.valid())
            m_sway->run({QStringLiteral("output"), name, QStringLiteral("mode"), swayModeArg(previous.width, previous.height, previous.mHz)});
    };
    const SwayRunner::Result r = m_sway->run({QStringLiteral("output"), name, QStringLiteral("mode"), swayModeArg(target.width, target.height, target.mHz)});
    if (!r.ok) {
        revertTo();
        fail(tr("The display refused %1: %2").arg(modeText(target), r.error));
        return false;
    }
    // swaymsg's exit code is not proof: read the output back, twice, a moment apart.
    for (int pass = 0; pass < 2; ++pass) {
        QList<DisplayOutput> outs;
        bool holds = false;
        if (readOutputs(&outs))
            for (const DisplayOutput &o : outs)
                if (o.name == name) holds = o.active && sameMode(o.current, target);
        if (!holds) {
            revertTo();
            fail(tr("The display did not take %1. Went back to the previous setting.").arg(modeText(target)));
            return false;
        }
        if (pass == 0 && m_settleMs > 0) m_sleep(m_settleMs);
    }
    return true;
}

DisplayController::ApplyReport DisplayController::applyAll(const ApplyOptions &opt) {
    ApplyReport rep;
    QList<DisplayOutput> outs;
    if (!readOutputs(&outs)) { rep.errors << m_error; return rep; }
    m_outputs = outs;
    const QMap<QString, DisplayEntry> saved = m_cfg.read();
    for (const DisplayOutput &o : outs) {
        const DisplayEntry se = saved.value(o.key());
        const QList<DisplayMode> rejected = m_rejected.value(o.key());
        const NativeChoice nc = chooseNative(o, rejected);
        DisplayEntry write;
        write.key = o.key();

        // ---- mode ----
        DisplayMode target = nc.mode;
        bool fromSaved = false;
        if (se.hasMode()) {
            DisplayMode s;
            s.width = se.width; s.height = se.height; s.mHz = se.mHz;
            bool offered = false;
            for (const DisplayMode &m : o.modes) if (sameMode(m, s)) { s = m; offered = true; break; }
            if (offered && !rejected.contains(s)) { target = s; fromSaved = true; }
        }
        DisplayMode now = o.current;
        if (target.valid() && !sameMode(target, o.current)) {
            const QString what = QStringLiteral("%1: mode %2").arg(o.name, swayModeArg(target.width, target.height, target.mHz));
            if (fromSaved || opt.unsaved == ApplyOptions::Immediate) {
                if (setModeChecked(o.name, target, o.current)) {
                    rep.changed << what; now = target;
                    write.width = target.width; write.height = target.height; write.mHz = target.mHz;
                } else {
                    rep.reverted << what;
                    rep.errors << m_error;
                    m_rejected[o.key()] << target;
                }
            } else if (opt.unsaved == ApplyOptions::Trial && m_declined.contains(o.key())) {
                // The person said no to this monitor already: do not ask again this session.
            } else if (opt.unsaved == ApplyOptions::Trial && !m_trial.active) {
                if (startTrial(o, target)) rep.deferred << what + QStringLiteral(" (waiting for confirmation)");
                else { rep.reverted << what; rep.errors << m_error; }
            } else {
                rep.deferred << what;
            }
        } else if (target.valid() && !se.hasMode()) {
            write.width = target.width; write.height = target.height; write.mHz = target.mHz;  // already native: remember it
        }

        // ---- scale (instant, no confirmation) ----
        double wantScale = se.hasScale() ? se.scale : recommendedScale(o, nc.resolution.isValid() ? nc.resolution : QSize(now.width, now.height));
        if (qAbs(wantScale - o.scale) > 0.005) {
            const SwayRunner::Result r = m_sway->run({QStringLiteral("output"), o.name, QStringLiteral("scale"), scaleArg(wantScale)});
            if (r.ok) { rep.changed << QStringLiteral("%1: scale %2").arg(o.name, scaleArg(wantScale)); write.scale = wantScale; }
            else { rep.errors << tr("%1: scale refused: %2").arg(o.name, r.error); }
        } else if (!se.hasScale()) {
            write.scale = wantScale;
        }
        if (!se.hasScale() || !se.hasMode()) {
            if (write.hasMode() || write.hasScale()) {
                QString err;
                if (!m_cfg.upsert(write, &err)) rep.errors << err;
            }
        }
    }
    refresh();
    return rep;
}

bool DisplayController::setScale(const QString &output, double scale) {
    const DisplayOutput *o = find(output);
    if (!o) { fail(tr("Unknown display %1").arg(output)); return false; }
    scale = qBound(0.5, scale, 4.0);
    const SwayRunner::Result r = m_sway->run({QStringLiteral("output"), output, QStringLiteral("scale"), scaleArg(scale)});
    if (!r.ok) { fail(tr("The display refused that scale: %1").arg(r.error)); return false; }
    DisplayEntry e;
    e.key = o->key();
    e.scale = scale;
    QString err;
    if (!m_cfg.upsert(e, &err)) fail(err);
    refresh();
    emit announcement(tr("Scale %1 percent").arg(scalePercent(scale)));
    return true;
}

bool DisplayController::startTrial(const DisplayOutput &o, const DisplayMode &target) {
    if (m_trial.active) { fail(tr("Finish the pending display change first.")); return false; }
    if (!setModeChecked(o.name, target, o.current)) { refresh(); return false; }
    m_trial = Trial{true, o.name, o.key(), o.label(), o.current, target, m_now() + qint64(kConfirmSeconds) * 1000};
    m_timer.start();
    refresh();
    emit trialChanged();
    emit announcement(tr("Keep this display setting? %1. It goes back in %2 seconds. Press Escape to go back now.").arg(modeText(target)).arg(kConfirmSeconds));
    return true;
}

void DisplayController::endTrial() {
    m_timer.stop();
    m_trial = Trial{};
    emit trialChanged();
}

bool DisplayController::setRefresh(const QString &output, int mHz) {
    const DisplayOutput *o = find(output);
    if (!o) { fail(tr("Unknown display %1").arg(output)); return false; }
    const NativeChoice nc = chooseNative(*o);
    if (!nc.resolution.isValid()) { fail(tr("This display does not report its modes.")); return false; }
    DisplayMode target;
    for (const DisplayMode &m : o->modes)
        if (m.width == nc.resolution.width() && m.height == nc.resolution.height() && m.mHz == mHz) target = m;
    if (!target.valid()) { fail(tr("That refresh rate is not available.")); return false; }
    if (sameMode(target, o->current)) return true;
    const DisplayOutput copy = *o;
    return startTrial(copy, target);
}

bool DisplayController::useRecommended(const QString &output) {
    const DisplayOutput *op = find(output);
    if (!op) { fail(tr("Unknown display %1").arg(output)); return false; }
    const DisplayOutput o = *op;
    m_declined.remove(o.key());
    m_rejected.remove(o.key());  // an explicit request: forget what this session turned down
    const NativeChoice nc = chooseNative(o);
    const double rec = recommendedScale(o, nc.resolution.isValid() ? nc.resolution : QSize(o.current.width, o.current.height));
    bool ok = true;
    if (qAbs(rec - o.scale) > 0.005) ok = setScale(output, rec);
    if (!nc.mode.valid()) return ok;
    if (sameMode(nc.mode, o.current)) {
        DisplayEntry e;
        e.key = o.key(); e.width = nc.mode.width; e.height = nc.mode.height; e.mHz = nc.mode.mHz; e.scale = rec;
        QString err;
        if (!m_cfg.upsert(e, &err)) fail(err);
        refresh();
        if (qAbs(rec - o.scale) <= 0.005) emit announcement(tr("Already using the recommended settings"));
        return ok;
    }
    return startTrial(o, nc.mode) && ok;
}

void DisplayController::confirm() {
    if (!m_trial.active) return;
    const Trial t = m_trial;
    DisplayEntry e;
    e.key = t.key; e.width = t.target.width; e.height = t.target.height; e.mHz = t.target.mHz;
    if (const DisplayOutput *o = find(t.output)) e.scale = o->scale;
    QString err;
    if (!m_cfg.upsert(e, &err)) fail(err);
    endTrial();
    emit announcement(tr("Display setting kept"));
}

void DisplayController::revert() {
    if (!m_trial.active) return;
    const Trial t = m_trial;
    m_declined << t.key;
    m_rejected[t.key] << t.target;  // do not offer it again this session (no loop on the next output event)
    endTrial();
    DisplayMode none;
    setModeChecked(t.output, t.previous, none);
    refresh();
    emit announcement(tr("Went back to the previous display setting"));
}

void DisplayController::poll() {
    if (m_trial.active && m_now() >= m_trial.deadline) { revert(); return; }
    if (m_trial.active) emit trialChanged();  // the countdown text
}
