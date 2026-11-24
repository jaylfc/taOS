// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Test support: a private PipeWire + WirePlumber + pipewire-pulse in a throw-away
// runtime directory, started and stopped by the test. Needs the three daemons
// and pw-cli, pw-dump, pacat on PATH; otherwise the caller skips.
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QtTest>

class PrivatePipeWire {
public:
    static bool toolsPresent() {
        for (const char *t : {"pipewire", "wireplumber", "pipewire-pulse", "pw-cli", "pw-dump", "pacat"})
            if (QStandardPaths::findExecutable(QString::fromLatin1(t)).isEmpty()) return false;
        return true;
    }
    bool start() {
        if (!m_dir.isValid()) return false;
        QFile::setPermissions(m_dir.path(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        for (const char *d : {"pipewire", "wireplumber", "pipewire-pulse"}) {
            auto *p = new QProcess;
            p->setProcessEnvironment(env());
            p->setStandardOutputFile(m_dir.filePath(QString::fromLatin1(d) + ".log"));
            p->setStandardErrorFile(m_dir.filePath(QString::fromLatin1(d) + ".log"));
            p->start(QStandardPaths::findExecutable(QString::fromLatin1(d)), {});
            if (!p->waitForStarted(5000)) return false;
            m_procs << p;
            if (QLatin1String(d) == QLatin1String("pipewire")) {
                if (!waitForFile(m_dir.filePath(QStringLiteral("pipewire-0")))) return false;
            }
        }
        return waitForFile(m_dir.filePath(QStringLiteral("pulse/native")));
    }
    ~PrivatePipeWire() {  // only the processes this object started
        for (QProcess *p : std::as_const(m_extra)) {
            p->terminate();
            if (!p->waitForFinished(3000)) { p->kill(); p->waitForFinished(2000); }
            delete p;
        }
        for (int i = int(m_procs.size()) - 1; i >= 0; --i) {
            m_procs[i]->terminate();
            if (!m_procs[i]->waitForFinished(4000)) { m_procs[i]->kill(); m_procs[i]->waitForFinished(2000); }
            delete m_procs[i];
        }
    }
    QString server() const { return QStringLiteral("unix:") + m_dir.filePath(QStringLiteral("pulse/native")); }
    QProcessEnvironment env() const {
        QProcessEnvironment e = QProcessEnvironment::systemEnvironment();
        e.insert(QStringLiteral("XDG_RUNTIME_DIR"), m_dir.path());
        e.insert(QStringLiteral("PIPEWIRE_RUNTIME_DIR"), m_dir.path());
        e.insert(QStringLiteral("PULSE_RUNTIME_PATH"), m_dir.filePath(QStringLiteral("pulse")));
        e.insert(QStringLiteral("PULSE_SERVER"), server());
        e.remove(QStringLiteral("DISPLAY"));
        e.insert(QStringLiteral("DBUS_SESSION_BUS_ADDRESS"), QString());
        return e;
    }
    // A virtual device that outlives the pw-cli call; returns its node id.
    int createNode(const QString &name, const QString &description, bool source) {
        const QString props = QStringLiteral("{ factory.name=support.null-audio-sink node.name=%1 node.description=\"%2\" media.class=%3 "
                                             "object.linger=true audio.position=[FL FR] }")
                                  .arg(name, description, source ? QStringLiteral("Audio/Source") : QStringLiteral("Audio/Sink"));
        run(QStringLiteral("pw-cli"), {QStringLiteral("create-node"), QStringLiteral("adapter"), props});
        return nodeId(name);
    }
    void destroyNode(int id) { run(QStringLiteral("pw-cli"), {QStringLiteral("destroy"), QString::number(id)}); }
    int nodeId(const QString &name) {
        const QByteArray out = run(QStringLiteral("pw-dump"), {});
        const auto arr = QJsonDocument::fromJson(out).array();
        for (const auto &v : arr) {
            const QJsonObject o = v.toObject();
            if (o.value("info").toObject().value("props").toObject().value("node.name").toString() == name) return o.value("id").toInt();
        }
        return -1;
    }
    QByteArray run(const QString &prog, const QStringList &args) {
        QProcess p;
        p.setProcessEnvironment(env());
        p.start(QStandardPaths::findExecutable(prog), args);
        p.waitForFinished(8000);
        return p.readAllStandardOutput();
    }
    // A virtual microphone that really delivers (silent) audio: a loopback whose far side is a source.
    QProcess *startVirtualMic(const QString &name, const QString &description) {
        auto *p = new QProcess;
        p->setProcessEnvironment(env());
        p->start(QStandardPaths::findExecutable(QStringLiteral("pw-loopback")),
                 {QStringLiteral("-n"), name + QStringLiteral("-loop"),
                  QStringLiteral("--capture-props=media.class=Audio/Sink node.name=%1-in node.description=\"%2 input\"").arg(name, description),
                  QStringLiteral("--playback-props=media.class=Audio/Source node.name=%1 node.description=\"%2\"").arg(name, description)});
        p->waitForStarted(5000);
        m_extra << p;
        return p;
    }
    // A stream that is playing (silence) until the object is destroyed.
    QProcess *startPlayer() {
        auto *p = new QProcess;
        p->setProcessEnvironment(env());
        p->setStandardInputFile(QStringLiteral("/dev/zero"));
        p->start(QStandardPaths::findExecutable(QStringLiteral("pacat")),
                 {QStringLiteral("-p"), QStringLiteral("--raw"), QStringLiteral("--rate=8000"), QStringLiteral("--channels=1"),
                  QStringLiteral("--format=s16le"), QStringLiteral("--client-name=test-player")});
        p->waitForStarted(5000);
        return p;
    }
private:
    static bool waitForFile(const QString &f) {
        for (int i = 0; i < 200; ++i) {
            if (QFileInfo::exists(f)) { QTest::qWait(300); return true; }
            QTest::qWait(50);
        }
        return false;
    }
    QTemporaryDir m_dir{QStringLiteral("/tmp/taos-pwtest-XXXXXX")};
    QList<QProcess *> m_procs, m_extra;
};
