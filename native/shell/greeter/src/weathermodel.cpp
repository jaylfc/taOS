// SPDX-License-Identifier: AGPL-3.0-or-later
#include "weathermodel.h"

#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <cmath>

QString WeatherModel::defaultPath() {
    const QString e = qEnvironmentVariable("TAOS_WEATHER_FILE");
    return e.isEmpty() ? QStringLiteral("/var/cache/taos/weather.json") : e;
}

WeatherModel::WeatherModel(QObject *parent) : QObject(parent) {
    m_path = defaultPath();
    m_timer.setInterval(10 * 60 * 1000);
    connect(&m_timer, &QTimer::timeout, this, &WeatherModel::refresh);
    m_timer.start();
}

QString WeatherModel::iconForCode(int c) {
    if (textForCode(c).isEmpty()) return QString();  // only the codes WMO defines
    if (c == 0 || c == 1) return QStringLiteral("clear");
    if (c == 2) return QStringLiteral("partly-cloudy");
    if (c == 3) return QStringLiteral("cloudy");
    if (c == 45 || c == 48) return QStringLiteral("fog");
    if (c >= 51 && c <= 57) return QStringLiteral("drizzle");
    if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return QStringLiteral("rain");
    if ((c >= 71 && c <= 77) || c == 85 || c == 86) return QStringLiteral("snow");
    if (c == 95 || c == 96 || c == 99) return QStringLiteral("thunder");
    return QString();
}

QString WeatherModel::textForCode(int c) {
    switch (c) {
    case 0: return QStringLiteral("Clear");
    case 1: return QStringLiteral("Mostly clear");
    case 2: return QStringLiteral("Partly cloudy");
    case 3: return QStringLiteral("Overcast");
    case 45: return QStringLiteral("Fog");
    case 48: return QStringLiteral("Freezing fog");
    case 51: return QStringLiteral("Light drizzle");
    case 53: return QStringLiteral("Drizzle");
    case 55: return QStringLiteral("Heavy drizzle");
    case 56: case 57: return QStringLiteral("Freezing drizzle");
    case 61: return QStringLiteral("Light rain");
    case 63: return QStringLiteral("Rain");
    case 65: return QStringLiteral("Heavy rain");
    case 66: case 67: return QStringLiteral("Freezing rain");
    case 71: return QStringLiteral("Light snow");
    case 73: return QStringLiteral("Snow");
    case 75: return QStringLiteral("Heavy snow");
    case 77: return QStringLiteral("Snow grains");
    case 80: case 81: return QStringLiteral("Rain showers");
    case 82: return QStringLiteral("Heavy showers");
    case 85: case 86: return QStringLiteral("Snow showers");
    case 95: return QStringLiteral("Thunderstorm");
    case 96: case 99: return QStringLiteral("Thunderstorm with hail");
    }
    return QString();
}

WeatherModel::Reading WeatherModel::parse(const QByteArray &json, const QDateTime &now) {
    Reading r;
    if (json.isEmpty() || json.size() > 64 * 1024) return r;
    const QJsonDocument d = QJsonDocument::fromJson(json);
    if (!d.isObject()) return r;
    const QJsonObject o = d.object();
    const QJsonValue t = o.value(QStringLiteral("temperature"));
    const QJsonValue c = o.value(QStringLiteral("code"));
    if (!t.isDouble() || !c.isDouble()) return r;
    const double temp = t.toDouble();
    if (!std::isfinite(temp) || temp < -90 || temp > 70 * 2) return r;
    const double cd = c.toDouble();
    if (cd != std::floor(cd) || cd < 0 || cd > 99) return r;
    r.code = int(cd);
    // updated: ISO 8601 text, or epoch seconds.
    const QJsonValue u = o.value(QStringLiteral("updated"));
    if (u.isString()) r.updated = QDateTime::fromString(u.toString(), Qt::ISODate);
    else if (u.isDouble() && u.toDouble() > 1e9) r.updated = QDateTime::fromSecsSinceEpoch(qint64(u.toDouble()), Qt::UTC);
    if (!r.updated.isValid()) return r;
    const qint64 age = r.updated.secsTo(now);
    if (age > kMaxAgeSecs || age < -600) return r;  // stale, or from the future (clock skew)
    r.icon = iconForCode(r.code);
    r.condition = o.value(QStringLiteral("condition")).toString().trimmed().left(48);
    if (r.condition.isEmpty()) r.condition = textForCode(r.code);
    if (r.icon.isEmpty() || r.condition.isEmpty()) return r;
    r.place = o.value(QStringLiteral("place")).toString().trimmed().left(48);
    r.isDay = o.value(QStringLiteral("is_day")).toVariant().toInt() != 0;
    r.temperature = QString::number(int(std::lround(temp))) + QChar(0x00B0);
    r.valid = true;
    return r;
}

QString WeatherModel::icon() const {
    if (!m_r.valid) return QString();
    if (m_r.icon == QLatin1String("clear") || m_r.icon == QLatin1String("partly-cloudy"))
        return m_r.icon + (m_r.isDay ? QStringLiteral("-day") : QStringLiteral("-night"));
    return m_r.icon;
}

void WeatherModel::refresh() {
    Reading r;
    QFile f(m_path);
    if (f.open(QIODevice::ReadOnly)) r = parse(f.read(65 * 1024), QDateTime::currentDateTimeUtc());
    const bool diff = r.valid != m_r.valid || r.temperature != m_r.temperature || r.icon != m_r.icon ||
                      r.condition != m_r.condition || r.place != m_r.place || r.isDay != m_r.isDay;
    m_r = r;
    if (diff) emit changed();
}
