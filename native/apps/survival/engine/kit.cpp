// SPDX-License-Identifier: AGPL-3.0-or-later
#include "kit.h"

#include <QLocale>

namespace Taos::Survival {

namespace {
QString sizeText(qint64 b) { return QLocale().formattedDataSize(b, 1, QLocale::DataSizeSIFormat); }

QJsonObject evalItem(const QJsonObject &item, const KitContext &c)
{
    const QString type = item.value(QStringLiteral("check")).toString();
    const double min = item.value(QStringLiteral("min")).toDouble(1);
    QString status = QStringLiteral("unknown"), detail;
    if (type == QLatin1String("zim")) {
        status = c.zimPacks >= min ? QStringLiteral("ok") : QStringLiteral("missing");
        detail = c.zimPacks > 0 ? QStringLiteral("%1 library pack(s) installed").arg(c.zimPacks) : QStringLiteral("No library pack installed. Get one in Library.");
    } else if (type == QLatin1String("map")) {
        status = c.mapPacks >= min ? QStringLiteral("ok") : QStringLiteral("missing");
        detail = c.mapPacks > 0 ? QStringLiteral("%1 map pack(s) installed").arg(c.mapPacks) : QStringLiteral("No map pack installed. Get one in Map.");
    } else if (type == QLatin1String("radio")) {
        if (c.radiosConfirmed >= min) { status = QStringLiteral("ok"); detail = QStringLiteral("%1 radio(s) confirmed").arg(c.radiosConfirmed); }
        else if (c.radiosPossible >= min) { status = QStringLiteral("unknown"); detail = QStringLiteral("%1 USB serial device(s) found that could be a radio. Check them in taOStak.").arg(c.radiosPossible); }
        else { status = QStringLiteral("missing"); detail = QStringLiteral("No radio connected"); }
    } else if (type == QLatin1String("battery")) {
        if (c.batteries < 0) { detail = QStringLiteral("This machine did not say whether it has a battery"); }
        else { status = c.batteries > 0 ? QStringLiteral("ok") : QStringLiteral("missing"); detail = c.batteries > 0 ? QStringLiteral("Battery present") : QStringLiteral("No battery: runs on mains power only"); }
    } else if (type == QLatin1String("storage")) {
        const qint64 need = qint64(min * 1024.0 * 1024.0 * 1024.0);
        if (c.freeBytes < 0) { detail = QStringLiteral("Free space could not be read"); }
        else { status = c.freeBytes >= need ? QStringLiteral("ok") : QStringLiteral("missing");
               detail = QStringLiteral("%1 free, %2 wanted").arg(sizeText(c.freeBytes), sizeText(need)); }
    } else if (type == QLatin1String("position")) {
        if (c.positionSources < 0) { detail = QStringLiteral("Location service not available to this app"); }
        else { status = c.positionSources > 0 ? QStringLiteral("ok") : QStringLiteral("missing");
               detail = c.positionSources > 0 ? QStringLiteral("A position source is available") : QStringLiteral("No GPS or location service found. Set your position by hand."); }
    } else {
        detail = item.value(QStringLiteral("note")).toString();
        if (detail.isEmpty()) detail = QStringLiteral("Cannot be checked from here yet");
    }
    QJsonObject o{{QStringLiteral("id"), item.value(QStringLiteral("id"))}, {QStringLiteral("title"), item.value(QStringLiteral("title"))},
                  {QStringLiteral("detail"), detail}, {QStringLiteral("status"), status}};
    return o;
}
} // namespace

QJsonArray evaluateKits(const QJsonObject &manifest, const KitContext &ctx)
{
    QJsonArray out;
    for (const QJsonValue &kv : manifest.value(QStringLiteral("kits")).toArray()) {
        const QJsonObject k = kv.toObject();
        QJsonArray items;
        int ready = 0;
        for (const QJsonValue &iv : k.value(QStringLiteral("items")).toArray()) {
            const QJsonObject r = evalItem(iv.toObject(), ctx);
            if (r.value(QStringLiteral("status")).toString() == QLatin1String("ok"))
                ++ready;
            items.append(r);
        }
        out.append(QJsonObject{{QStringLiteral("id"), k.value(QStringLiteral("id"))}, {QStringLiteral("title"), k.value(QStringLiteral("title"))},
                               {QStringLiteral("hardware"), k.value(QStringLiteral("hardware"))}, {QStringLiteral("summary"), k.value(QStringLiteral("summary"))},
                               {QStringLiteral("ready"), ready}, {QStringLiteral("total"), items.size()}, {QStringLiteral("items"), items}});
    }
    return out;
}

} // namespace Taos::Survival
