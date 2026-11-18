#include "theme.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

static QJsonObject readJson(const QString &path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return {};
    QJsonParseError err;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &err);
    return err.error == QJsonParseError::NoError ? d.object() : QJsonObject();
}

// "#RRGGBBAA" (CSS order) to QColor; QColor itself reads "#AARRGGBB".
static QColor cssColor(const QString &s, const QColor &fallback) {
    if (s.size() == 9 && s.startsWith(QLatin1Char('#'))) {
        bool ok = false;
        const uint v = s.mid(1).toUInt(&ok, 16);
        if (!ok) return fallback;
        return QColor::fromRgb(int((v >> 24) & 0xff), int((v >> 16) & 0xff), int((v >> 8) & 0xff), int(v & 0xff));
    }
    const QColor c(s);
    return c.isValid() ? c : fallback;
}

bool Theme::load(const QString &id, const QString &variant, const QStringList &searchDirs,
                 const QString &builtinPath, bool reduceMotionFlag, QString *error) {
    QJsonObject sys;
    m_source = QStringLiteral("builtin");
    for (const QString &d : searchDirs) {
        if (d.isEmpty()) continue;
        const QString p = d + QLatin1Char('/') + id + QStringLiteral("/theme.json");
        const QJsonObject o = readJson(p);
        if (!o.isEmpty()) {
            sys = o;
            m_source = p;
            break;
        }
    }
    const QJsonObject builtin = readJson(builtinPath);
    m_id = sys.isEmpty() ? builtin.value(QStringLiteral("id")).toString() : id;

    // Variant must be listed by the theme (the built-in one when no file).
    const QJsonObject &primary = sys.isEmpty() ? builtin : sys;
    bool listed = false;
    for (const QJsonValue &v : primary.value(QStringLiteral("variants")).toArray())
        listed = listed || v.toString() == variant;
    m_variant = (listed || primary.value(QStringLiteral("variants")).toArray().isEmpty()) ? variant : QStringLiteral("dark");
    if (m_variant != QLatin1String("light")) m_variant = QStringLiteral("dark");

    const QJsonObject sysCols = sys.value(QStringLiteral("colors")).toObject().value(m_variant).toObject();
    const QJsonObject bltCols = builtin.value(QStringLiteral("colors")).toObject().value(m_variant).toObject();
    auto col = [&](const char *key) -> QColor {
        const QString k = QString::fromLatin1(key);
        QColor c = cssColor(sysCols.value(k).toString(), QColor());
        if (!c.isValid()) c = cssColor(bltCols.value(k).toString(), QColor());
        return c;
    };
    m_ground = col("ground");
    m_stroke = col("stroke");
    m_text = col("text");
    m_text2 = col("textSecondary");
    m_text3 = col("textTertiary");
    m_accent = col("accent");
    m_warning = col("warning");
    m_danger = col("danger");
    if (!m_ground.isValid() || !m_text.isValid() || !m_accent.isValid() || !m_stroke.isValid()) {
        if (error) *error = QStringLiteral("no usable colours in theme '%1' or the built-in theme").arg(id);
        return false;
    }
    if (!m_text2.isValid()) m_text2 = m_text;
    if (!m_text3.isValid()) m_text3 = m_text2;
    if (!m_warning.isValid()) m_warning = m_accent;
    if (!m_danger.isValid()) m_danger = m_accent;

    m_bar = m_ground;
    m_bar.setAlphaF(kBarOpacity);
    m_hover = m_text;
    m_hover.setAlphaF(0.10);
    m_pressed = m_text;
    m_pressed.setAlphaF(0.18);

    const bool themeReduce = sys.value(QStringLiteral("motion")).toObject().value(QStringLiteral("reduce")).toBool(
        builtin.value(QStringLiteral("motion")).toObject().value(QStringLiteral("reduce")).toBool(false));
    m_reduce = reduceMotionFlag || themeReduce;
    return true;
}
