#include "frameconfig.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>

QStringList FrameConfig::builtinExclude()
{
    return {QStringLiteral("^my\\.taos\\."), QStringLiteral("^(chromium|chromium-browser|google-chrome.*)$")};
}

static bool matchesAny(const QStringList &patterns, const QString &s)
{
    for (const QString &p : patterns) {
        const QRegularExpression re(p);
        if (re.isValid() && re.match(s).hasMatch())
            return true;
    }
    return false;
}

bool FrameConfig::shouldFrame(const QString &appId) const
{
    if (appId.isEmpty())
        return false;
    if (matchesAny(builtinExclude(), appId) || matchesAny(exclude, appId))
        return false;
    return frameAllForeign || matchesAny(include, appId);
}

QString FrameConfigFile::userPath()
{
    const QString env = qEnvironmentVariable("TAOS_FRAME_CONFIG");
    if (!env.isEmpty())
        return env;
    const QString xdg = qEnvironmentVariable("XDG_CONFIG_HOME");
    const QString base = xdg.isEmpty() ? QDir::homePath() + QStringLiteral("/.config") : xdg;
    return base + QStringLiteral("/taos/frame.json");
}

static QStringList validPatterns(const QJsonValue &v, QStringList *bad)
{
    QStringList out;
    for (const QJsonValue &e : v.toArray()) {
        const QString p = e.toString();
        if (p.isEmpty())
            continue;
        if (QRegularExpression(p).isValid())
            out << p;
        else
            bad->append(p);
    }
    return out;
}

bool FrameConfigFile::read(const QString &path, FrameConfig *out, QString *error)
{
    *out = FrameConfig();
    QFile f(path);
    if (!f.exists())
        return true;
    if (!f.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot read %1").arg(path);
        return false;
    }
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    if (!d.isObject()) {
        if (error) *error = QStringLiteral("%1 is not a JSON object (%2)").arg(path, pe.errorString());
        return false;
    }
    const QJsonObject o = d.object();
    QStringList bad;
    if (o.contains(QLatin1String("include")))
        out->include = validPatterns(o.value(QLatin1String("include")), &bad);
    if (o.contains(QLatin1String("exclude")))
        out->exclude = validPatterns(o.value(QLatin1String("exclude")), &bad);
    out->frameAllForeign = o.value(QLatin1String("frameAllForeign")).toBool(false);
    if (!bad.isEmpty() && error)
        *error = QStringLiteral("ignored invalid patterns: %1").arg(bad.join(QLatin1String(", ")));
    return true;
}
