#pragma once
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>

// An isolated home for one test: nothing here may read the real ~/.config or ~/.local.
struct TestHome {
    QTemporaryDir dir;
    QString appearance() const { return dir.path() + "/config/taos/appearance.json"; }
    QString userThemes() const { return dir.path() + "/data/taos/themes"; }
    QString systemThemes() const { return dir.path() + "/sys/themes"; }
    QString systemAppearance() const { return dir.path() + "/sys/appearance.json"; }
    QString mirror() const { return dir.path() + "/mirror"; }
    TestHome() {
        qputenv("TAOS_APPEARANCE", appearance().toUtf8());
        qputenv("XDG_DATA_HOME", (dir.path() + "/data").toUtf8());
        qputenv("TAOS_SYSTEM_THEME_DIR", systemThemes().toUtf8());
        qputenv("TAOS_SYSTEM_APPEARANCE", systemAppearance().toUtf8());
        qputenv("TAOS_APPEARANCE_MIRROR_DIR", mirror().toUtf8());
        qunsetenv("TAOS_THEME_DIR");
        QDir().mkpath(systemThemes());
    }
};

// A theme document: a plain dark theme that passes validation. Edit the object to break it.
inline QJsonObject goodTheme(const QString &id, const QString &accent = QStringLiteral("#7FB2FF")) {
    auto variant = [&](bool dark) {
        QJsonObject c;
        c["ground"] = dark ? "#101012" : "#EEEEF0";
        c["surface"] = dark ? "#18181B" : "#F7F7F9";
        c["raised"] = dark ? "#1F1F23" : "#FFFFFF";
        c["stroke"] = dark ? "#FFFFFF14" : "#00000017";
        c["hairline"] = dark ? "#FFFFFF14" : "#00000017";
        c["text"] = dark ? "#EDEDEE" : "#161617";
        c["textSecondary"] = dark ? "#A3A3A8" : "#55555A";
        c["textTertiary"] = dark ? "#85858A" : "#66666B";
        c["accent"] = accent;
        c["accentText"] = dark ? "#101012" : "#101012";
        c["warning"] = dark ? "#D1A257" : "#8A5E00";
        c["danger"] = dark ? "#E0786F" : "#B3392F";
        c["ok"] = dark ? "#6FBF8E" : "#2B7650";
        c["series1"] = accent;
        c["series2"] = "#FFFFFF59";
        c["selection"] = "#FFFFFF1A";
        c["focusRing"] = accent;
        c["live"] = accent;
        c["track"] = "#FFFFFF14";
        c["scrollbar"] = "#FFFFFF38";
        return c;
    };
    QJsonObject o;
    o["id"] = id;
    o["name"] = "Theme " + id;
    o["description"] = "A test theme.";
    o["variants"] = QJsonArray{"dark", "light"};
    o["colors"] = QJsonObject{{"dark", variant(true)}, {"light", variant(false)}};
    return o;
}

inline void writeTheme(const QString &dir, const QString &folder, const QJsonObject &o) {
    QDir().mkpath(dir + "/" + folder);
    QFile f(dir + "/" + folder + "/theme.json");
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) qFatal("cannot write test theme");
    f.write(QJsonDocument(o).toJson());
}
