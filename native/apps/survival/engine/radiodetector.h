// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Finds serial devices that could be mesh radios and, only when asked, checks one with a read-only handshake.
//
// Scanning reads sysfs and /dev only: it never opens a port. A USB ID tells the chip, not the firmware (a Heltec or a
// RAK board shows as a generic USB serial bridge), so a scan result is only ever "possible radio". The Meshtastic
// handshake (a ToRadio want_config_id request, answered by a FromRadio stream, framed 0x94 0xC3 + 16 bit length) is
// what confirms it. The handshake opens the port, and opening a port can restart some boards, so it runs on one device
// at a time, on an explicit request, never from a scan. Nothing here transmits on the radio.
#include <QByteArray>
#include <QList>
#include <QString>
#include <memory>

namespace Taos::Survival {

struct RadioCandidate
{
    QString device;       // /dev/ttyACM0
    QString byId;         // the /dev/serial/by-id name when there is one
    quint16 vid = 0;
    quint16 pid = 0;
    QString vendor;       // as the USB descriptor says
    QString product;
    QString serial;
    QString chip;         // what the USB ID is known to be, e.g. "Espressif USB serial (ESP32-S3)"
    QString boards;       // boards that commonly use it
    QString confidence;   // possible | unlikely
    bool inUse = false;   // another process has the port open
    // filled in by a probe
    bool probed = false;
    QString protocol;     // meshtastic when the handshake was answered
    QString firmware;
    QString probeNote;    // plain words about the probe
};

struct ProbeResult
{
    bool identified = false;
    QString protocol;
    QString firmware;
    QString note;
};

/// The serial side of a probe, replaceable so tests and previews never touch hardware.
class SerialIo
{
public:
    virtual ~SerialIo() = default;
    virtual bool open(QString *error) = 0;
    virtual bool write(const QByteArray &data) = 0;
    /// Reads whatever arrives within `ms`.
    virtual QByteArray read(int ms) = 0;
    virtual void close() = 0;
};

class RadioDetector
{
public:
    explicit RadioDetector(QString sysRoot = QStringLiteral("/sys"), QString devRoot = QStringLiteral("/dev"),
                           QString procRoot = QStringLiteral("/proc"));
    /// USB serial devices, newest first by name. Opens nothing.
    QList<RadioCandidate> scan() const;
    /// Opens the real port and runs the Meshtastic handshake.
    ProbeResult probe(const RadioCandidate &c) const;
    /// The same handshake over any SerialIo (what the tests drive).
    static ProbeResult probeWith(SerialIo &io);
    /// The bytes sent: 32 x 0xC3 to wake a sleeping device, then a framed ToRadio{want_config_id}.
    static QByteArray meshtasticRequest(quint32 nonce = 69420);
    /// Reads a received byte stream: does it hold at least one valid FromRadio frame, and what does it tell.
    static ProbeResult parseMeshtasticStream(const QByteArray &bytes);
    /// What a USB id is known to be. Empty chip when unknown.
    static void describeUsbId(quint16 vid, quint16 pid, QString *chip, QString *boards, QString *confidence);

private:
    bool portInUse(const QString &device) const;
    QString m_sys, m_dev, m_proc;
};

} // namespace Taos::Survival
