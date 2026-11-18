#include "volume.h"

#include <QRegularExpression>
#include <QStandardPaths>
#include <cmath>

Volume::Reading Volume::parse(const QString &output) {
    Reading r;
    static const QRegularExpression re(QStringLiteral(R"(^\s*Volume:\s*([0-9]+(?:\.[0-9]+)?))"));
    const auto m = re.match(output);
    if (!m.hasMatch()) return r;
    r.ok = true;
    r.percent = int(std::lround(m.captured(1).toDouble() * 100.0));
    r.muted = output.contains(QLatin1String("[MUTED]"));
    return r;
}

Volume::Volume(const QString &wpctl, QObject *parent) : QObject(parent), m_wpctl(wpctl) {
    if (m_wpctl.isEmpty()) m_wpctl = QStandardPaths::findExecutable(QStringLiteral("wpctl"));
    if (m_wpctl.isEmpty()) return;  // hidden, no timer, no process

    connect(&m_proc, &QProcess::finished, this, [this] {
        const Reading r = parse(QString::fromUtf8(m_proc.readAllStandardOutput()));
        const bool avail = r.ok;
        if (avail != m_available || r.percent != m_percent || r.muted != m_muted) {
            m_available = avail;
            m_percent = r.percent;
            m_muted = r.muted;
            emit changed();
        }
    });
    connect(&m_proc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        if (m_available) {
            m_available = false;
            emit changed();
        }
    });
    m_timer.setInterval(5000);
    m_timer.setTimerType(Qt::VeryCoarseTimer);
    connect(&m_timer, &QTimer::timeout, this, &Volume::refresh);
    m_timer.start();
    refresh();
}

QString Volume::description() const {
    if (!m_available) return {};
    return m_muted ? tr("Volume muted") : tr("Volume %1 percent").arg(m_percent);
}

void Volume::refresh() {
    if (m_wpctl.isEmpty() || m_proc.state() != QProcess::NotRunning) return;
    m_proc.start(m_wpctl, {QStringLiteral("get-volume"), QStringLiteral("@DEFAULT_AUDIO_SINK@")});
}
