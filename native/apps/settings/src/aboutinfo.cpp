#include "aboutinfo.h"

#include <QFile>
#include <QProcess>
#include <QRegularExpression>
#include <QStorageInfo>
#include <QSysInfo>
#include <QVariantMap>

using namespace Qt::StringLiterals;

namespace TaosSettings {

static QString readFile(const QString &path)
{
    QFile f(path);
    return f.open(QIODevice::ReadOnly) ? QString::fromUtf8(f.readAll()) : QString();
}

AboutInfo::AboutInfo(const QString &root, const QString &storagePath, QObject *parent)
    : QObject(parent), m_root(root), m_storage(storagePath)
{
    refresh();
}

QString AboutInfo::formatBytes(qint64 b)
{
    const double gb = double(b) / 1e9;
    if (gb >= 1000)
        return QObject::tr("%1 TB").arg(gb / 1000, 0, 'f', 1);
    if (gb >= 10)
        return QObject::tr("%1 GB").arg(qRound(gb));
    return QObject::tr("%1 GB").arg(gb, 0, 'f', 1);
}

void AboutInfo::refresh()
{
    // Device name: /etc/hostname when present, else the system's.
    QString host = readFile(m_root + u"/etc/hostname"_s).trimmed();
    if (host.isEmpty())
        host = QSysInfo::machineHostName();
    m_device = host;

    QString os;
    const QString rel = readFile(m_root + u"/etc/os-release"_s);
    static const QRegularExpression pretty(u"^PRETTY_NAME=\"?([^\"\\n]*)\"?"_s, QRegularExpression::MultilineOption);
    const auto m = pretty.match(rel);
    os = m.hasMatch() ? m.captured(1) : QSysInfo::prettyProductName();
    m_os = os;

    QString cpu;
    int cores = 0;
    const QString ci = readFile(m_root + u"/proc/cpuinfo"_s);
    static const QRegularExpression modelRe(u"^(?:model name|Model|Hardware)\\s*:\\s*(.+)$"_s, QRegularExpression::MultilineOption);
    const auto cm = modelRe.match(ci);
    if (cm.hasMatch())
        cpu = cm.captured(1).trimmed();
    static const QRegularExpression procRe(u"^processor\\s*:"_s, QRegularExpression::MultilineOption);
    auto it = procRe.globalMatch(ci);
    while (it.hasNext()) { it.next(); ++cores; }
    if (cores == 0 && ci.isEmpty())
        cores = 0;

    qint64 memKb = 0;
    const QString mi = readFile(m_root + u"/proc/meminfo"_s);
    static const QRegularExpression memRe(u"^MemTotal:\\s*(\\d+)\\s*kB"_s, QRegularExpression::MultilineOption);
    const auto mm = memRe.match(mi);
    if (mm.hasMatch())
        memKb = mm.captured(1).toLongLong();

    QVariantList rows;
    const auto add = [&](const QString &l, const QString &v) { if (!v.isEmpty()) rows << QVariantMap{{u"label"_s, l}, {u"value"_s, v}}; };
    add(tr("System"), os);
    add(tr("Kernel"), QSysInfo::kernelType() + u' ' + QSysInfo::kernelVersion());
    add(tr("Processor"), cpu.isEmpty() ? QString() : (cores > 0 ? tr("%1, %2 cores").arg(cpu).arg(cores) : cpu));
    add(tr("Memory"), memKb > 0 ? formatBytes(memKb * 1024) : QString());
    QStorageInfo st(m_storage.isEmpty() ? QStringLiteral("/") : m_storage);
    if (st.isValid() && st.bytesTotal() > 0)
        add(tr("Storage"), tr("%1 free of %2").arg(formatBytes(st.bytesAvailable()), formatBytes(st.bytesTotal())));
    add(tr("Architecture"), QSysInfo::currentCpuArchitecture());
    m_rows = rows;
    emit changed();
}

bool AboutInfo::openActivity() const
{
    return QProcess::startDetached(u"taos-dashboard"_s, {});
}

} // namespace TaosSettings
