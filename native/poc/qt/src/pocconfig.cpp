#include "pocconfig.h"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QDirIterator>

PocConfig::PocConfig(QObject *parent) : QObject(parent)
{
    // Wallpaper: POC_WALLPAPER, else ../common/wallpaper.png relative to the
    // source dir; the binary lives in <src>/out/<base>/, so that is ../../../common.
    QString wp = qEnvironmentVariable("POC_WALLPAPER");
    if (wp.isEmpty()) {
        const QString dir = QCoreApplication::applicationDirPath();
        const QStringList cands = { dir + "/../../../common/wallpaper.png",
                                    dir + "/../common/wallpaper.png",
                                    dir + "/wallpaper.png" };
        for (const QString &c : cands)
            if (QFileInfo::exists(c)) { wp = c; break; }
        if (wp.isEmpty()) wp = cands.first();
    }
    m_wallpaper = QUrl::fromLocalFile(QFileInfo(wp).absoluteFilePath());

    m_noBlur = qEnvironmentVariable("POC_NO_BLUR") == QLatin1String("1");
    bool ok = false;
    int r = qEnvironmentVariable("POC_BLUR_RADIUS").toInt(&ok);
    if (ok && r >= 0) m_blurRadius = r;
    m_autoCard = qMax(0, qEnvironmentVariable("POC_AUTOCARD").toInt());
    m_autoScroll = qMax(0, qEnvironmentVariable("POC_AUTOSCROLL").toInt());

    // Battery: first power_supply with a capacity file of type Battery.
    m_battery = QStringLiteral("--%");
    QDirIterator it("/sys/class/power_supply", QDir::Dirs | QDir::NoDotAndDotDot);
    while (it.hasNext()) {
        const QString d = it.next();
        QFile type(d + "/type"), cap(d + "/capacity");
        if (type.open(QIODevice::ReadOnly) && type.readAll().trimmed() == "Battery"
            && cap.open(QIODevice::ReadOnly)) {
            m_battery = QString::fromLatin1(cap.readAll().trimmed()) + QLatin1Char('%');
            break;
        }
    }
}
