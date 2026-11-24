#include "windowinfo.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <algorithm>

bool WindowInfo::operator==(const WindowInfo &o) const {
    return id == o.id && appId == o.appId && title == o.title && pid == o.pid && workspace == o.workspace
        && focused == o.focused && minimised == o.minimised && floating == o.floating
        && fullscreen == o.fullscreen && fullSize == o.fullSize && output == o.output;
}

namespace WindowJson {

QString minMark(int conId) { return QStringLiteral("taos_min_%1").arg(conId); }

bool parseList(const QByteArray &json, QVector<WindowInfo> *out, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isArray()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not a JSON array");
        return false;
    }
    out->clear();
    for (const QJsonValue &v : d.array()) {
        const QJsonObject o = v.toObject();
        if (!o.contains(QStringLiteral("id"))) continue;
        WindowInfo w;
        w.id = o.value(QStringLiteral("id")).toInt();
        w.appId = o.value(QStringLiteral("app_id")).toString();
        w.title = o.value(QStringLiteral("title")).toString();
        w.pid = o.value(QStringLiteral("pid")).toInt();
        w.workspace = o.value(QStringLiteral("workspace")).toVariant().toString();
        w.focused = o.value(QStringLiteral("focused")).toBool();
        w.minimised = o.value(QStringLiteral("minimised")).toBool();
        w.floating = o.value(QStringLiteral("floating")).toBool();
        w.fullscreen = o.value(QStringLiteral("fullscreen")).toBool();
        // Not in the contract today (taos-window keeps it in the taos_full mark): read it when the engine adds it.
        w.fullSize = o.value(QStringLiteral("full_size")).toBool(o.value(QStringLiteral("maximised")).toBool());
        w.output = o.value(QStringLiteral("output")).toString();
        if (w.id > 0) out->append(w);
    }
    return true;
}

QByteArray toJson(const QVector<WindowInfo> &ws) {
    QJsonArray a;
    for (const WindowInfo &w : ws) {
        QJsonObject o;
        o[QStringLiteral("id")] = w.id;
        o[QStringLiteral("app_id")] = w.appId;
        o[QStringLiteral("title")] = w.title;
        o[QStringLiteral("pid")] = w.pid;
        o[QStringLiteral("workspace")] = w.workspace;
        o[QStringLiteral("focused")] = w.focused;
        o[QStringLiteral("minimised")] = w.minimised;
        o[QStringLiteral("floating")] = w.floating;
        o[QStringLiteral("fullscreen")] = w.fullscreen;
        if (w.fullSize) o[QStringLiteral("full_size")] = true;
        o[QStringLiteral("output")] = w.output;
        a.append(o);
    }
    return QJsonDocument(a).toJson(QJsonDocument::Compact);
}

static void walk(const QJsonObject &n, const QString &output, const QString &workspace, QVector<WindowInfo> *out) {
    const QString type = n.value(QStringLiteral("type")).toString();
    QString out2 = output, ws2 = workspace;
    if (type == QLatin1String("output")) out2 = n.value(QStringLiteral("name")).toString();
    if (type == QLatin1String("workspace")) ws2 = n.value(QStringLiteral("name")).toString();
    const QJsonArray kids = n.value(QStringLiteral("nodes")).toArray();
    const QJsonArray floats = n.value(QStringLiteral("floating_nodes")).toArray();
    const bool leaf = kids.isEmpty() && floats.isEmpty();
    if (leaf && (type == QLatin1String("con") || type == QLatin1String("floating_con"))) {
        const QJsonObject wp = n.value(QStringLiteral("window_properties")).toObject();
        QString appId = n.value(QStringLiteral("app_id")).toString();
        if (appId.isEmpty()) appId = wp.value(QStringLiteral("class")).toString();
        const int pid = n.value(QStringLiteral("pid")).toInt();
        if (!appId.isEmpty() || pid > 0) {
            WindowInfo w;
            w.id = n.value(QStringLiteral("id")).toInt();
            w.appId = appId;
            w.title = n.value(QStringLiteral("name")).toString();
            w.pid = pid;
            w.workspace = ws2 == QLatin1String("__i3_scratch") ? QString() : ws2;
            w.focused = n.value(QStringLiteral("focused")).toBool();
            w.floating = type == QLatin1String("floating_con");
            w.fullscreen = n.value(QStringLiteral("fullscreen_mode")).toInt() != 0;
            w.output = out2 == QLatin1String("__i3") ? QString() : out2;
            const QString mark = minMark(w.id);
            for (const QJsonValue &m : n.value(QStringLiteral("marks")).toArray())
                if (m.toString() == mark) w.minimised = true;
            if (w.id > 0) out->append(w);
        }
        return;
    }
    for (const QJsonValue &c : kids) walk(c.toObject(), out2, ws2, out);
    for (const QJsonValue &c : floats) walk(c.toObject(), out2, ws2, out);
}

bool parseSwayTree(const QByteArray &json, QVector<WindowInfo> *out, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not a JSON object");
        return false;
    }
    out->clear();
    walk(d.object(), QString(), QString(), out);
    std::sort(out->begin(), out->end(), [](const WindowInfo &a, const WindowInfo &b) { return a.id < b.id; });
    return true;
}

bool focusedWorkspace(const QByteArray &json, QString *name) {
    const QJsonDocument d = QJsonDocument::fromJson(json);
    if (!d.isArray()) return false;
    for (const QJsonValue &v : d.array()) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("focused")).toBool()) { *name = o.value(QStringLiteral("name")).toString(); return true; }
    }
    return false;
}

}  // namespace WindowJson
