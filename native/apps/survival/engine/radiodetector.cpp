// SPDX-License-Identifier: AGPL-3.0-or-later
#include "radiodetector.h"

#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QSet>
#include <QThread>

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

namespace Taos::Survival {

namespace {
QString readTrim(const QString &path)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return {};
    return QString::fromUtf8(f.readAll()).trimmed();
}

bool readVarint(const QByteArray &b, int &i, quint64 &v)
{
    v = 0;
    for (int shift = 0; shift < 64 && i < b.size(); shift += 7) {
        const quint8 c = quint8(b[i++]);
        v |= quint64(c & 0x7f) << shift;
        if (!(c & 0x80))
            return true;
    }
    return false;
}

// Reads the top level of a protobuf message; fills `fields` with the field numbers seen and `strings` with the
// length-delimited payloads. False when it does not parse as protobuf.
bool scanProto(const QByteArray &b, QSet<int> &fields, QHash<int, QByteArray> *payloads = nullptr)
{
    int i = 0;
    while (i < b.size()) {
        quint64 key;
        if (!readVarint(b, i, key))
            return false;
        const int field = int(key >> 3), wire = int(key & 7);
        if (field <= 0 || field > 1000)
            return false;
        fields.insert(field);
        switch (wire) {
        case 0: { quint64 v; if (!readVarint(b, i, v)) return false; break; }
        case 1: if (i + 8 > b.size()) return false; i += 8; break;
        case 5: if (i + 4 > b.size()) return false; i += 4; break;
        case 2: {
            quint64 len;
            if (!readVarint(b, i, len) || len > quint64(b.size() - i))
                return false;
            if (payloads)
                payloads->insert(field, b.mid(i, int(len)));
            i += int(len);
            break;
        }
        default: return false;
        }
    }
    return true;
}

class PosixSerial : public SerialIo
{
public:
    explicit PosixSerial(QString dev) : m_dev(std::move(dev)) {}
    ~PosixSerial() override { close(); }
    bool open(QString *error) override
    {
        m_fd = ::open(m_dev.toLocal8Bit().constData(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (m_fd < 0) {
            if (error) *error = QStringLiteral("Cannot open the port: %1").arg(QString::fromLocal8Bit(std::strerror(errno)));
            return false;
        }
        // exclusive: refuse (rather than share) a port another program is using
        if (::ioctl(m_fd, TIOCEXCL) < 0 && errno == EBUSY) {
            ::close(m_fd); m_fd = -1;
            if (error) *error = QStringLiteral("Another program is using this port.");
            return false;
        }
        termios t{};
        if (::tcgetattr(m_fd, &t) == 0) {
            ::cfmakeraw(&t);
            ::cfsetispeed(&t, B115200);
            ::cfsetospeed(&t, B115200);
            t.c_cflag |= CLOCAL | CREAD;
            t.c_cflag &= ~tcflag_t(HUPCL);   // do not drop DTR when we close
            ::tcsetattr(m_fd, TCSANOW, &t);
        }
        // keep the control lines low: many boards restart on a DTR/RTS pulse
        int bits = TIOCM_DTR | TIOCM_RTS;
        ::ioctl(m_fd, TIOCMBIC, &bits);
        return true;
    }
    bool write(const QByteArray &d) override
    {
        qsizetype off = 0;
        QElapsedTimer t; t.start();
        while (off < d.size() && t.elapsed() < 2000) {
            const ssize_t n = ::write(m_fd, d.constData() + off, size_t(d.size() - off));
            if (n > 0) off += n;
            else if (errno == EAGAIN) QThread::msleep(5);
            else return false;
        }
        return off == d.size();
    }
    QByteArray read(int ms) override
    {
        QByteArray out;
        QElapsedTimer t; t.start();
        char buf[1024];
        while (t.elapsed() < ms) {
            const ssize_t n = ::read(m_fd, buf, sizeof buf);
            if (n > 0) { out.append(buf, n); if (out.size() > 64 * 1024) break; }
            else QThread::msleep(10);
        }
        return out;
    }
    void close() override { if (m_fd >= 0) { ::close(m_fd); m_fd = -1; } }
private:
    QString m_dev;
    int m_fd = -1;
};
} // namespace

RadioDetector::RadioDetector(QString sysRoot, QString devRoot, QString procRoot)
    : m_sys(std::move(sysRoot)), m_dev(std::move(devRoot)), m_proc(std::move(procRoot)) {}

void RadioDetector::describeUsbId(quint16 vid, quint16 pid, QString *chip, QString *boards, QString *confidence)
{
    struct Row { quint16 vid, pid; const char *chip; const char *boards; };
    static const Row rows[] = {
        {0x303a, 0x1001, "Espressif USB serial (ESP32-S3 or C3)", "Heltec V3, LilyGO T-Beam S3, T-Deck, Seeed boards"},
        {0x10c4, 0xea60, "Silicon Labs CP210x USB serial bridge", "Heltec V2, LilyGO T-Beam, many ESP32 boards"},
        {0x1a86, 0x7523, "WCH CH340 USB serial bridge", "LilyGO and Heltec boards, RNode clones"},
        {0x1a86, 0x55d4, "WCH CH9102 USB serial bridge", "LilyGO T-Beam, Heltec boards"},
        {0x0403, 0x6001, "FTDI USB serial bridge", "RNode and custom boards"},
        {0x0403, 0x6015, "FTDI USB serial bridge", "RNode and custom boards"},
    };
    for (const Row &r : rows) {
        if (r.vid == vid && r.pid == pid) {
            *chip = QString::fromLatin1(r.chip); *boards = QString::fromLatin1(r.boards);
            *confidence = QStringLiteral("possible");
            return;
        }
    }
    if (vid == 0x239a) { *chip = QStringLiteral("Adafruit nRF52 USB serial"); *boards = QStringLiteral("RAK4631 (WisBlock), LilyGO T-Echo, Heltec T114"); *confidence = QStringLiteral("possible"); return; }
    if (vid == 0x2886) { *chip = QStringLiteral("Seeed Studio USB serial"); *boards = QStringLiteral("SenseCAP T1000-E, XIAO boards"); *confidence = QStringLiteral("possible"); return; }
    if (vid == 0x303a) { *chip = QStringLiteral("Espressif USB device"); *boards = QStringLiteral("ESP32-based boards"); *confidence = QStringLiteral("possible"); return; }
    if (vid == 0x2e8a) { *chip = QStringLiteral("Raspberry Pi RP2040 USB serial"); *boards = QStringLiteral("Pico, RAK11310"); *confidence = QStringLiteral("possible"); return; }
    chip->clear(); boards->clear();
    *confidence = QStringLiteral("unlikely");
}

bool RadioDetector::portInUse(const QString &device) const
{
    // Another process holding the port shows up as a /proc/<pid>/fd link to it (only our own processes are readable).
    const QDir proc(m_proc);
    const QString real = QFileInfo(device).canonicalFilePath();
    if (real.isEmpty())
        return false;
    for (const QString &pid : proc.entryList(QDir::Dirs | QDir::NoDotAndDotDot)) {
        bool num = false;
        pid.toInt(&num);
        if (!num)
            continue;
        const QDir fd(m_proc + QLatin1Char('/') + pid + QStringLiteral("/fd"));
        for (const QString &n : fd.entryList(QDir::AllEntries | QDir::System | QDir::NoDotAndDotDot))
            if (QFileInfo(fd.filePath(n)).symLinkTarget() == real)
                return true;
    }
    return false;
}

QList<RadioCandidate> RadioDetector::scan() const
{
    QList<RadioCandidate> out;
    const QDir tty(m_sys + QStringLiteral("/class/tty"));
    QStringList names = tty.entryList({QStringLiteral("ttyUSB*"), QStringLiteral("ttyACM*")}, QDir::AllEntries | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString &name : std::as_const(names)) {
        RadioCandidate c;
        c.device = m_dev + QLatin1Char('/') + name;
        // walk up from the interface to the USB device that carries idVendor
        QDir d(QFileInfo(tty.filePath(name) + QStringLiteral("/device")).canonicalFilePath());
        for (int depth = 0; depth < 6 && !d.exists(QStringLiteral("idVendor")); ++depth)
            if (!d.cdUp())
                break;
        if (!d.exists(QStringLiteral("idVendor")))
            continue;   // not a USB device (a built-in serial port): not a radio
        bool ok1 = false, ok2 = false;
        c.vid = quint16(readTrim(d.filePath(QStringLiteral("idVendor"))).toUInt(&ok1, 16));
        c.pid = quint16(readTrim(d.filePath(QStringLiteral("idProduct"))).toUInt(&ok2, 16));
        if (!ok1 || !ok2)
            continue;
        c.vendor = readTrim(d.filePath(QStringLiteral("manufacturer")));
        c.product = readTrim(d.filePath(QStringLiteral("product")));
        c.serial = readTrim(d.filePath(QStringLiteral("serial")));
        describeUsbId(c.vid, c.pid, &c.chip, &c.boards, &c.confidence);
        const QDir byId(m_dev + QStringLiteral("/serial/by-id"));
        for (const QString &l : byId.entryList(QDir::AllEntries | QDir::NoDotAndDotDot))
            if (QFileInfo(byId.filePath(l)).canonicalFilePath() == QFileInfo(c.device).canonicalFilePath() || QFileInfo(byId.filePath(l)).symLinkTarget().endsWith(QLatin1Char('/') + name))
                c.byId = l;
        c.inUse = portInUse(c.device);
        out.append(c);
    }
    return out;
}

QByteArray RadioDetector::meshtasticRequest(quint32 nonce)
{
    // ToRadio { want_config_id = 3 } : tag 0x18, then the varint
    QByteArray payload;
    payload.append(char(0x18));
    quint32 v = nonce;
    do { quint8 b = v & 0x7f; v >>= 7; if (v) b |= 0x80; payload.append(char(b)); } while (v);
    QByteArray out(32, char(0xC3));   // wake a sleeping device
    out.append(char(0x94)).append(char(0xC3));
    out.append(char((payload.size() >> 8) & 0xff)).append(char(payload.size() & 0xff));
    out.append(payload);
    return out;
}

ProbeResult RadioDetector::parseMeshtasticStream(const QByteArray &b)
{
    ProbeResult r;
    int i = 0;
    int frames = 0;
    while (i + 4 <= b.size()) {
        if (quint8(b[i]) != 0x94 || quint8(b[i + 1]) != 0xC3) { ++i; continue; }
        const int len = (quint8(b[i + 2]) << 8) | quint8(b[i + 3]);
        if (len > 512) { ++i; continue; }          // the documented sign of a corrupt frame
        if (i + 4 + len > b.size()) break;         // incomplete
        const QByteArray payload = b.mid(i + 4, len);
        QSet<int> fields;
        QHash<int, QByteArray> data;
        if (len > 0 && scanProto(payload, fields, &data)) {
            // FromRadio: my_info 3, node_info 4, config 5, config_complete_id 7, moduleConfig 9, channel 10, metadata 13
            static const QList<int> known{3, 4, 5, 7, 9, 10, 13};
            for (int f : known) {
                if (fields.contains(f)) {
                    ++frames;
                    r.identified = true;
                    if (f == 13) {
                        QSet<int> inner; QHash<int, QByteArray> innerData;
                        if (scanProto(data.value(13), inner, &innerData) && innerData.contains(1))
                            r.firmware = QString::fromUtf8(innerData.value(1));
                    }
                    break;
                }
            }
        }
        i += 4 + len;
    }
    if (r.identified) {
        r.protocol = QStringLiteral("meshtastic");
        r.note = QStringLiteral("Meshtastic answered the handshake (%1 frame(s)).").arg(frames);
    }
    return r;
}

ProbeResult RadioDetector::probeWith(SerialIo &io)
{
    ProbeResult r;
    QString err;
    if (!io.open(&err)) {
        r.note = err;
        return r;
    }
    if (!io.write(meshtasticRequest())) {
        r.note = QStringLiteral("Could not write to the port.");
        io.close();
        return r;
    }
    QByteArray got;
    QElapsedTimer t; t.start();
    while (t.elapsed() < 4000) {
        got += io.read(300);
        r = parseMeshtasticStream(got);
        if (r.identified)
            break;
    }
    io.close();
    if (!r.identified)
        r.note = got.isEmpty() ? QStringLiteral("No answer. This is not a Meshtastic radio, it is asleep, or it speaks another protocol.")
                               : QStringLiteral("It answered, but not with the Meshtastic protocol.");
    return r;
}

ProbeResult RadioDetector::probe(const RadioCandidate &c) const
{
    if (portInUse(c.device)) {
        ProbeResult r;
        r.note = QStringLiteral("Another program has this port open, so it was not touched.");
        return r;
    }
    PosixSerial io(c.device);
    return probeWith(io);
}

} // namespace Taos::Survival
