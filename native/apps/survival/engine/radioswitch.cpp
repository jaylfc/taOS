// SPDX-License-Identifier: AGPL-3.0-or-later
#include "radioswitch.h"

#include <QJsonArray>
#include <QJsonObject>

using namespace Qt::Literals::StringLiterals;

namespace Taos::Survival {

namespace {
QVariant valueAt(const QVariantMap &values, const QString &path)
{
    const QStringList parts = path.split(QLatin1Char('.'));
    if (parts.size() == 3 && parts[0] == u"channels"_s) {
        for (const QVariant &c : values.value(u"channels"_s).toList())
            if (c.toMap().value(u"index"_s).toString() == parts[1])
                return c.toMap().value(parts[2]);
        return {};
    }
    QVariant cur = values;
    for (const QString &p : parts) {
        if (cur.typeId() != QMetaType::QVariantMap)
            return {};
        cur = cur.toMap().value(p);
    }
    return cur;
}
} // namespace

RadioSwitch::RadioSwitch(MeshBackend *mesh, FirmwareClient *fw, RadioProfiles *profiles, QObject *parent) : QObject(parent), m_b(mesh), m_c(fw), m_p(profiles) {}

QString RadioSwitch::stepName(Step s)
{
    switch (s) {
    case Step::Idle: return u"idle"_s;
    case Step::Saving: return u"saving"_s;
    case Step::Releasing: return u"releasing"_s;
    case Step::Flashing: return u"flashing"_s;
    case Step::Reconnecting: return u"reconnecting"_s;
    case Step::Restoring: return u"restoring"_s;
    case Step::Done: return u"done"_s;
    case Step::Failed: return u"failed"_s;
    }
    return {};
}

void RadioSwitch::go(Step s)
{
    m_step = s;
    Q_EMIT changed();
}

void RadioSwitch::note(const QString &text)
{
    m_notes << text;
    Q_EMIT changed();
}

void RadioSwitch::fail(const QString &why)
{
    ++m_run;   // whatever was still waiting stops
    m_failedAt = m_step;
    m_error = why;
    if (m_recovery.isEmpty())
        m_recovery = tr("Nothing has been lost: the old settings are saved on this device. If the board does not come up, unplug it, hold its boot button while plugging it back in, and put the previous firmware back from the firmware library.");
    m_step = Step::Failed;
    Q_EMIT changed();
}

void RadioSwitch::waitFor(std::function<bool()> cond, int timeoutMs, std::function<void(bool)> then)
{
    const quint64 run = m_run;
    auto *t = new QTimer(this);
    auto *left = new QElapsedTimer;
    left->start();
    t->setInterval(120);
    connect(t, &QTimer::timeout, this, [this, t, left, run, cond, timeoutMs, then] {
        if (run != m_run) {
            t->deleteLater();
            delete left;
            return;
        }
        const bool ok = cond();
        if (ok || left->elapsed() > timeoutMs) {
            t->deleteLater();
            delete left;
            then(ok);
        }
    });
    t->start();
}

MeshRadio RadioSwitch::radioOnPort() const
{
    for (const MeshRadio &r : m_b->radios())
        if (!m_req.port.isEmpty() && r.port == m_req.port)
            return r;
    return {};
}

void RadioSwitch::start(const SwitchRequest &req)
{
    if (running())
        return;
    ++m_run;
    m_req = req;
    m_error.clear();
    m_recovery.clear();
    m_notes.clear();
    m_flash.clear();
    m_profileSaved = false;
    m_restored = 0;
    m_failedAt = Step::Idle;
    m_newRadio.clear();
    m_clock.start();
    if (req.deviceId.isEmpty() || req.entryId.isEmpty() || req.typedName.trimmed().isEmpty()) {
        m_step = Step::Saving;
        fail(tr("Choose the board and the firmware, and type the board name, first."));
        return;
    }
    save();
}

// ---- 1: save what the radio is set to on the old network -------------------------------------------------------------------------
void RadioSwitch::save()
{
    go(Step::Saving);
    if (m_req.radioId.isEmpty() || m_req.fromNetwork.isEmpty()) {
        note(tr("The mesh engine has no open connection to this board, so its settings were not read. The firmware manager still saves its own backup."));
        release();
        return;
    }
    m_b->requestDeviceConfig(m_req.radioId);
    const quint64 run = m_run;
    waitFor([this] { const QString s = m_b->deviceConfig(m_req.radioId).state; return s == u"ok"_s || s == u"unsupported"_s || s == u"error"_s; }, m_configMs, [this, run](bool) {
        if (run != m_run)
            return;
        const MeshDeviceConfig c = m_b->deviceConfig(m_req.radioId);
        if (c.state != u"ok"_s) {
            note(tr("The radio's settings could not be read (%1). The firmware manager still saves its own backup.").arg(c.note.isEmpty() ? c.state : c.note));
            release();
            return;
        }
        QString err;
        MeshRadio r;
        for (const MeshRadio &x : m_b->radios())
            if (x.id == m_req.radioId)
                r = x;
        if (!m_p->save(m_req.deviceId, m_req.fromNetwork, c.values, r.hardware, r.firmware, &err)) {
            fail(err);   // the person asked for the settings to be kept: do not go on without them
            return;
        }
        m_profileSaved = true;
        note(tr("Settings for %1 saved on this device.").arg(m_req.fromNetwork));
        release();
    });
}

// ---- 2: the mesh engine lets go of the port --------------------------------------------------------------------------------------
void RadioSwitch::release()
{
    go(Step::Releasing);
    for (const MeshDevice &d : m_b->devices())
        m_knownPorts << d.id;
    if (m_req.port.isEmpty()) {
        note(tr("The mesh engine had nothing open on this board."));
        flash();
        return;
    }
    const quint64 run = m_run;
    auto conn = std::make_shared<QMetaObject::Connection>();
    *conn = connect(m_b, &MeshBackend::actionDone, this, [this, run, conn](const QString &what, bool ok, const QString &err) {
        if (what != u"release:"_s + m_req.port)
            return;
        disconnect(*conn);
        if (run != m_run)
            return;
        if (!ok) {
            fail(tr("The mesh engine could not let go of the board: %1").arg(err));
            return;
        }
        note(tr("The mesh engine let go of the port."));
        flash();
    });
    m_b->releaseDevice(m_req.port);
}

// ---- 3: the firmware manager flashes (typed name, one-use owner permission, both checked by the manager) --------------------------
void RadioSwitch::flash()
{
    go(Step::Flashing);
    const quint64 run = m_run;
    m_c->mintNonce(u"flash.run"_s, m_req.deviceId + u"|"_s + m_req.entryId, this, [this, run](const QString &nonce, const QString &err) {
        if (run != m_run)
            return;
        if (nonce.isEmpty()) {
            fail(tr("The firmware manager did not give permission to flash: %1").arg(err));
            return;
        }
        QJsonObject body{{u"device"_s, m_req.deviceId}, {u"entry"_s, m_req.entryId}, {u"confirm"_s, m_req.typedName.trimmed()}, {u"owner_nonce"_s, nonce},
                         {u"actor"_s, u"person"_s}};   // exactly "person": the manager takes no other word for the human at the screen
        if (m_req.ackUnsigned)
            body.insert(u"ack_unsigned"_s, true);
        if (m_req.allowNoBackup)
            body.insert(u"allow_no_backup"_s, true);
        m_c->request(u"POST"_s, u"/v1/flash/run"_s, body, this, [this, run](const FwReply &r) {
            if (run != m_run)
                return;
            m_flash = r.json.toVariantMap();
            if (!r.reached) {
                fail(r.error);
                return;
            }
            if (r.http == 403) {
                fail(tr("The firmware manager refused: the owner permission was not accepted."));
                return;
            }
            const QString verify = r.json.value(u"verify"_s).toObject().value(u"status"_s).toString();
            if (!r.json.value(u"ok"_s).toBool() || verify == u"failed"_s) {
                const QString rec = r.json.value(u"recovery"_s).toString();
                if (!rec.isEmpty())
                    m_recovery = rec;
                fail(r.json.value(u"message"_s).toString().isEmpty() ? tr("The flash did not complete.") : r.json.value(u"message"_s).toString());
                return;
            }
            note(verify == u"confirmed"_s ? tr("The firmware was written and the board answered.") : tr("The firmware was written. The board could not be checked from here."));
            reconnect();
        }, 1800000);
    });
}

// ---- 4: find the board again and let the engine detect which network it speaks --------------------------------------------------
void RadioSwitch::reconnect()
{
    go(Step::Reconnecting);
    const quint64 run = m_run;
    auto opened = std::make_shared<bool>(false);
    auto lastTry = std::make_shared<qint64>(-100000);
    auto port = std::make_shared<QString>(m_req.port);
    waitFor([this, run, opened, lastTry, port] {
        if (run != m_run)
            return true;
        if (!port->isEmpty()) {
            for (const MeshRadio &x : m_b->radios())
                if (x.port == *port && x.state == u"connected"_s) {
                    m_newRadio = x.id;
                    return true;
                }
        }
        if (!*opened || m_clock.elapsed() - *lastTry > 4000) {
            m_b->discoverDevices();
            // the board may have come back on another port: take the same one if it is there, else the one new USB port
            QStringList usb, fresh;
            for (const MeshDevice &d : m_b->devices())
                if (d.kind == u"usb"_s) {
                    usb << d.id;
                    if (!m_knownPorts.contains(d.id))
                        fresh << d.id;
                }
            QString pick;
            if (!m_req.port.isEmpty() && usb.contains(m_req.port))
                pick = m_req.port;
            else if (fresh.size() == 1)
                pick = fresh.first();
            if (!pick.isEmpty()) {
                *port = pick;
                *opened = true;
                *lastTry = m_clock.elapsed();
                m_b->connectDevice(pick, QString());   // no network named: the engine's read-only handshake finds out
            }
        }
        return false;
    }, m_reconnectMs, [this, run, port](bool ok) {
        if (run != m_run)
            return;
        if (!ok || m_newRadio.isEmpty()) {
            fail(tr("The board did not come back on a port the mesh engine could open. Unplug it and plug it in again, then press Connect on the Radios page."));
            return;
        }
        MeshRadio r;
        for (const MeshRadio &x : m_b->radios())
            if (x.id == m_newRadio)
                r = x;
        if (!m_req.toNetwork.isEmpty() && r.transport != m_req.toNetwork) {
            fail(tr("The board came up speaking %1, not %2.").arg(r.transport, m_req.toNetwork));
            return;
        }
        note(tr("The board is back on %1.").arg(r.transport));
        restore();
    });
}

// ---- 5: put back the settings saved earlier for this network ---------------------------------------------------------------------
void RadioSwitch::restore()
{
    go(Step::Restoring);
    const QVariantMap profile = m_p->load(m_req.deviceId, m_req.toNetwork);
    if (profile.isEmpty()) {
        note(tr("No settings were saved for %1 before, so the new firmware keeps its own defaults.").arg(m_req.toNetwork));
        finish();
        return;
    }
    m_b->requestDeviceConfig(m_newRadio);
    const quint64 run = m_run;
    waitFor([this] { const QString s = m_b->deviceConfig(m_newRadio).state; return s == u"ok"_s || s == u"unsupported"_s || s == u"error"_s; }, m_configMs, [this, run, profile](bool) {
        if (run != m_run)
            return;
        const MeshDeviceConfig c = m_b->deviceConfig(m_newRadio);
        if (c.state != u"ok"_s) {
            note(tr("The earlier settings are kept, but the radio's settings could not be read to put them back (%1).").arg(c.note.isEmpty() ? c.state : c.note));
            finish();
            return;
        }
        const QVariantMap saved = profile.value(u"values"_s).toMap();
        QVariantMap changes;
        for (const QVariant &w : c.values.value(u"writable"_s).toList()) {
            QStringList paths;
            const QString t = w.toString();
            if (t.contains(u"<n>"_s)) {
                for (const QVariant &ch : saved.value(u"channels"_s).toList())
                    paths << QString(t).replace(u"<n>"_s, ch.toMap().value(u"index"_s).toString());
            } else {
                paths << t;
            }
            for (const QString &p : paths) {
                const QVariant want = valueAt(saved, p), have = valueAt(c.values, p);
                if (want.isValid() && !want.toString().isEmpty() && want.toString() != have.toString())
                    changes.insert(p, want);
            }
        }
        if (c.values.value(u"writable"_s).toList().isEmpty()) {
            note(tr("The earlier settings are kept. This radio's settings cannot be written from here yet, so they were not put back."));
            finish();
            return;
        }
        if (changes.isEmpty()) {
            note(tr("The radio already has the settings saved earlier."));
            finish();
            return;
        }
        auto conn = std::make_shared<QMetaObject::Connection>();
        *conn = connect(m_b, &MeshBackend::actionDone, this, [this, run, conn, n = int(changes.size())](const QString &what, bool ok, const QString &err) {
            if (what != u"device"_s)
                return;
            disconnect(*conn);
            if (run != m_run)
                return;
            if (!ok) {
                fail(tr("The earlier settings could not be put back: %1").arg(err));
                return;
            }
            m_restored = n;
            note(tr("%n earlier setting(s) put back. The radio may restart to use them.", "", n));
            finish();
        });
        m_b->applyDeviceConfig(m_newRadio, changes);
    });
}

void RadioSwitch::finish()
{
    ++m_run;
    go(Step::Done);
}

} // namespace Taos::Survival
