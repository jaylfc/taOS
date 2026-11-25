// SPDX-License-Identifier: AGPL-3.0-or-later
// A pair of stand-in engines for pictures and hand checks: the mesh engine's socket and the firmware manager's HTTP API, both
// answering from scripted data and the engines' captured JSON. They cannot transmit and cannot flash. Used only by
// shots.sh and by anyone running the app without radios; it is a test tool, never installed.
//   fake-survival-engines <scenario> [flash-delay-ms]      scenario: bare | one | two
// Needs XDG_RUNTIME_DIR (the mesh socket goes there as taos-mesh.sock), TAOS_FIRMWARE_DIR and TAOS_FIRMWARE_PORT.
// a test tool: plain string literals are fine here
#undef QT_NO_CAST_FROM_ASCII
#undef QT_NO_CAST_TO_ASCII
#include <QCoreApplication>
#include <QDir>
#include <QJsonArray>
#include <QJsonObject>
#include <QTextStream>

#include "fakemesh.h"

using namespace FakeEngines;

static QJsonObject radio(const QString &id, const QString &name, const QString &network, const QString &fw, const QString &hw, const QString &port, int battery)
{
    return {{"id", id}, {"name", name}, {"transport", network}, {"link", "usb"}, {"state", "connected"}, {"firmware", fw}, {"hardware", hw}, {"port", port}, {"batteryPercent", battery}};
}
static QJsonObject text(const QString &id, const QString &net, const QString &from, int ch, const QString &t, qint64 ago, const QString &who)
{
    return {{"broadcast", true}, {"channel", ch}, {"dir", "in"}, {"from", from}, {"host_ts_ms", double(QDateTime::currentMSecsSinceEpoch() - ago)}, {"id", id}, {"kind", "text"}, {"network", net},
            {"owner", "person:jay"}, {"radio", from}, {"text", t}, {"to", "!ffffffff"}, {"trust", "claimed"}, {"v", 1}, {"sender", QJsonObject{{"identity", "contact:c1"}, {"name", who}}},
            {"signal", QJsonObject{{"rssi", -91}, {"snr", 6.5}, {"hops", 1}}}};
}
static QJsonObject mtCfg()
{
    return {{"network", "meshtastic"}, {"firmware", "2.7.15.567b8ea"}, {"hardware", "Heltec V3"}, {"owner", QJsonObject{{"long_name", "Jay Heltec"}, {"short_name", "JH"}}},
            {"lora", QJsonObject{{"region", "EU_868"}, {"modem_preset", "LONG_FAST"}}}, {"device", QJsonObject{{"role", "CLIENT"}}}, {"position", QJsonObject{{"broadcast_secs", 900}}},
            {"channels", QJsonArray{QJsonObject{{"index", 0}, {"name", ""}, {"role", "PRIMARY"}, {"psk_b64", "AQ=="}, {"share_url", "https://meshtastic.org/e/#CgMSAQE"}},
                                    QJsonObject{{"index", 1}, {"name", "family"}, {"role", "SECONDARY"}, {"psk_b64", "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="}, {"share_url", "https://meshtastic.org/e/#CgMSAQEKDRIBAhoGZmFtaWx5"}}}},
            {"options", QJsonObject{{"region", QJsonArray{"US", "EU_433", "EU_868", "ANZ"}}, {"modem_preset", QJsonArray{"LONG_FAST", "LONG_SLOW", "MEDIUM_FAST", "SHORT_FAST"}}, {"role", QJsonArray{"CLIENT", "CLIENT_MUTE", "ROUTER"}}}},
            {"writable", QJsonArray{"owner.long_name", "owner.short_name", "lora.region", "lora.modem_preset", "device.role", "position.broadcast_secs", "channels.<n>.name"}}};
}
static QJsonObject mcCfg()
{
    return {{"network", "meshcore"}, {"firmware", "v1.9.1 (26 Nov 2026)"}, {"hardware", "Heltec V3"}, {"name", "Trail Node"},
            {"radio", QJsonObject{{"freq_mhz", 869.618}, {"bw_khz", 62.5}, {"sf", 8}, {"cr", 8}, {"tx_power_dbm", 20}}},
            {"channels", QJsonArray{QJsonObject{{"index", 0}, {"name", "Public"}, {"role", "PRIMARY"}, {"psk_b64", "izOH6cXN6mrJ5e26oRXNcg=="}}}}, {"writable", QJsonArray()}};
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QString scenario = argc > 1 ? QString::fromLocal8Bit(argv[1]) : QStringLiteral("one");
    const int delay = argc > 2 ? QString::fromLocal8Bit(argv[2]).toInt() : 0;
    const QString fx = QStringLiteral(MESH_FIXTURES_DIR);
    const QString run = qEnvironmentVariable("XDG_RUNTIME_DIR");
    const QString fwDir = qEnvironmentVariable("TAOS_FIRMWARE_DIR");
    if (run.isEmpty() || fwDir.isEmpty()) {
        QTextStream(stderr) << "XDG_RUNTIME_DIR and TAOS_FIRMWARE_DIR must be set\n";
        return 2;
    }
    FakeMeshEngine eng(fx + QStringLiteral("/mesh"));
    eng.scripted = true;
    eng.receiveOnly = false;
    eng.configReadable = true;
    eng.configWritable = true;
    eng.serialDevices = QJsonArray{QJsonObject{{"path", "/dev/ttyUSB0"}, {"vid", 0x10c4}, {"pid", 0xea60}, {"product", "Heltec V3"}}};
    if (scenario != QLatin1String("bare")) {
        eng.radiosJson.append(radio("!a1b2c3d4", "Jay Heltec", "meshtastic", "2.7.15.567b8ea", "Heltec V3", "/dev/ttyUSB0", 83));
        eng.cfgByRadio.insert("!a1b2c3d4", mtCfg());
        eng.serialDevices = QJsonArray();
    }
    QJsonArray people{QJsonObject{{"callsign", "Sam"}, {"hasPosition", true}, {"id", "!28af67cc"}, {"isSelf", false}, {"lastHeardMs", double(QDateTime::currentMSecsSinceEpoch() - 240000)}, {"lat", 51.45}, {"lon", -1.23}, {"owner", ""}}};
    QJsonArray msgs{text("m1", "meshtastic", "!28af67cc", 0, "Heading to the ridge, back by four", 300000, "Sam")};
    if (scenario == QLatin1String("two")) {
        eng.radiosJson.append(radio("a1b2c3d4e5f6", "Trail Node", "meshcore", "v1.9.1 (26 Nov 2026)", "Heltec V3", "/dev/ttyACM0", 61));
        eng.cfgByRadio.insert("a1b2c3d4e5f6", mcCfg());
        people.append(QJsonObject{{"callsign", "Ria"}, {"hasPosition", true}, {"id", "7ed23c00aabb"}, {"isSelf", false}, {"lastHeardMs", double(QDateTime::currentMSecsSinceEpoch() - 900000)}, {"lat", 51.47}, {"lon", -1.20}, {"owner", ""}});
        msgs.append(text("m2", "meshcore", "7ed23c00aabb", 0, "Repeater on the hill is up", 600000, "Ria"));
    }
    eng.identitiesJson = people;
    eng.textMessages = msgs;
    // flashing: the radio comes back as MeshCore when the app connects after the flash
    eng.onConnect = [&eng](const QString &) {
        if (!eng.radiosJson.isEmpty())
            return;
        eng.radiosJson.append(radio("a1b2c3d4e5f6", "Trail Node", "meshcore", "v1.9.1 (26 Nov 2026)", "Heltec V3", "/dev/ttyUSB0", -1));
        eng.cfgByRadio.insert("a1b2c3d4e5f6", mcCfg());
    };
    QDir().mkpath(run);
    if (!eng.listen(run + QStringLiteral("/taos-mesh.sock")))
        return 3;
    bool ok = false;
    const int port = qEnvironmentVariableIntValue("TAOS_FIRMWARE_PORT", &ok);
    FakeFirmware fw(fx + QStringLiteral("/firmware"), fwDir, nullptr, ok ? quint16(port) : quint16(0));
    fw.runDelayMs = delay;
    QTextStream(stdout) << "ready " << fw.port() << Qt::endl;
    return app.exec();
}
