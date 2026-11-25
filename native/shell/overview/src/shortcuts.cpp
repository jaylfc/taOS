// SPDX-License-Identifier: AGPL-3.0-or-later
#include "shortcuts.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QSet>
#include <algorithm>

namespace Shortcuts {

namespace {
struct Ctx {
    QMap<QString, QString> vars;
    QVector<Binding> all;
    QString floatMod;
    QStringList errors;
    QSet<QString> seen;   // files already read (include loops)
};

QString expand(const Ctx &c, QString s) {
    QStringList names = c.vars.keys();
    std::sort(names.begin(), names.end(), [](const QString &a, const QString &b) { return a.size() > b.size(); });
    for (const QString &n : names) s.replace(n, c.vars.value(n));
    return s;
}

void readFile(Ctx *c, const QString &path, int depth);

void doInclude(Ctx *c, const QString &fromFile, QString pattern, int depth) {
    pattern = expand(*c, pattern.trimmed());
    if (pattern.startsWith(QLatin1String("~/"))) pattern = QDir::homePath() + pattern.mid(1);
    if (QFileInfo(pattern).isRelative()) pattern = QFileInfo(fromFile).absolutePath() + QLatin1Char('/') + pattern;
    const QFileInfo fi(pattern);
    if (pattern.contains(QLatin1Char('*')) || pattern.contains(QLatin1Char('?'))) {
        const QStringList files = QDir(fi.absolutePath()).entryList({ fi.fileName() }, QDir::Files, QDir::Name);
        for (const QString &f : files) readFile(c, fi.absolutePath() + QLatin1Char('/') + f, depth + 1);
    } else {
        readFile(c, fi.absoluteFilePath(), depth + 1);
    }
}

void readFile(Ctx *c, const QString &path, int depth) {
    if (depth > 8 || c->seen.contains(path)) return;
    c->seen.insert(path);
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) { c->errors << QStringLiteral("cannot read %1").arg(path); return; }
    QStringList lines = QString::fromUtf8(f.readAll()).split(QLatin1Char('\n'));
    int braces = 0;   // inside a `mode "x" {` block: those bindings are for another mode
    for (int i = 0; i < lines.size(); ++i) {
        QString line = lines[i];
        while (line.endsWith(QLatin1Char('\\')) && i + 1 < lines.size()) { line.chop(1); line += lines[++i].trimmed(); }
        line = line.trimmed();
        if (line.isEmpty() || line.startsWith(QLatin1Char('#'))) continue;
        if (line.startsWith(QLatin1String("mode ")) && line.endsWith(QLatin1Char('{'))) { braces++; continue; }
        if (line == QLatin1String("}")) { if (braces > 0) braces--; continue; }
        if (line.endsWith(QLatin1Char('{'))) { braces++; continue; }   // bar { ... } and friends
        if (braces > 0) continue;
        const QString head = line.section(QRegularExpression(QStringLiteral("\\s+")), 0, 0);
        const QString rest = line.mid(head.size()).trimmed();
        if (head == QLatin1String("set")) {
            const QString name = rest.section(QRegularExpression(QStringLiteral("\\s+")), 0, 0);
            if (name.startsWith(QLatin1Char('$'))) c->vars.insert(name, expand(*c, rest.mid(name.size()).trimmed()));
        } else if (head == QLatin1String("include")) {
            doInclude(c, path, rest, depth);
        } else if (head == QLatin1String("floating_modifier")) {
            c->floatMod = expand(*c, rest).section(QRegularExpression(QStringLiteral("\\s+")), 0, 0);
        } else if (head == QLatin1String("bindsym")) {
            QStringList w = expand(*c, rest).split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
            Binding b;
            while (!w.isEmpty() && w.first().startsWith(QLatin1String("--"))) {
                const QString fl = w.takeFirst();
                if (fl == QLatin1String("--release")) b.release = true;
                else if (fl == QLatin1String("--locked")) b.locked = true;
                else if (fl == QLatin1String("--border") || fl == QLatin1String("--whole-window") || fl == QLatin1String("--exclude-titlebar")) b.combo.clear();
            }
            if (w.size() < 2) continue;
            b.combo = w.takeFirst();
            b.command = w.join(QLatin1Char(' '));
            b.file = path;
            c->all.push_back(b);
        }
    }
}
}  // namespace

ParseResult parseConfig(const QString &mainPath) {
    Ctx c;
    readFile(&c, QFileInfo(mainPath).absoluteFilePath(), 0);
    ParseResult r;
    // a later definition of the same combo replaces the earlier one
    for (const Binding &b : std::as_const(c.all)) {
        bool replaced = false;
        for (Binding &o : r.bindings)
            if (o.combo == b.combo && o.release == b.release) { o = b; replaced = true; }
        if (!replaced) r.bindings.push_back(b);
    }
    r.floatingModifier = c.floatMod;
    r.errors = c.errors;
    return r;
}

QString prettyKey(const QString &t) {
    static const QMap<QString, QString> m = {
        { "Mod4", "Super" }, { "Super", "Super" }, { "Super_L", "Super" }, { "Super_R", "Super" }, { "Mod1", "Alt" }, { "Alt", "Alt" },
        { "Alt_L", "Alt" }, { "Alt_R", "Alt" }, { "Control", "Ctrl" }, { "Ctrl", "Ctrl" }, { "Control_L", "Ctrl" }, { "Shift", "Shift" },
        { "Shift_L", "Shift" }, { "Return", "Enter" }, { "space", "Space" }, { "Escape", "Esc" }, { "Tab", "Tab" }, { "BackSpace", "Backspace" },
        { "Print", "Print Screen" }, { "XF86AudioRaiseVolume", "Volume up" }, { "XF86AudioLowerVolume", "Volume down" },
        { "XF86AudioMute", "Mute" }, { "XF86AudioMicMute", "Mic mute" }, { "XF86MonBrightnessUp", "Brightness up" },
        { "XF86MonBrightnessDown", "Brightness down" }, { "button1", "Left click" }, { "button2", "Middle click" },
        { "button3", "Right click" }, { "Up", "Up" }, { "Down", "Down" }, { "Left", "Left" }, { "Right", "Right" },
        { "Prior", "Page up" }, { "Next", "Page down" }, { "minus", "-" }, { "plus", "+" }, { "comma", "," }, { "period", "." },
    };
    if (m.contains(t)) return m.value(t);
    if (t.size() == 1) return t.toUpper();
    return t;
}

QStringList prettyKeys(const QString &combo, bool release) {
    QStringList out;
    for (const QString &t : combo.split(QLatin1Char('+'), Qt::SkipEmptyParts)) out << prettyKey(t);
    if (release && out.size() == 1 && (out[0] == QLatin1String("Super") || out[0] == QLatin1String("Alt"))) out[0] = QStringLiteral("Tap ") + out[0];
    return out;
}

bool Registry::load(const QByteArray &json, QString *error) {
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (!d.isObject()) { if (error) *error = pe.errorString(); return false; }
    const QJsonObject o = d.object();
    groupOrder.clear();
    for (const QJsonValue &v : o.value(QLatin1String("groups")).toArray()) groupOrder << v.toString();
    if (o.contains(QLatin1String("other"))) otherGroup = o.value(QLatin1String("other")).toString();
    m_entries.clear();
    for (const QJsonValue &v : o.value(QLatin1String("bindings")).toArray()) {
        const QJsonObject e = v.toObject();
        Entry en;
        en.match = QRegularExpression(QLatin1String("^(?:") + e.value(QLatin1String("match")).toString() + QLatin1String(")$"));
        if (!en.match.isValid()) { if (error) *error = QStringLiteral("bad pattern: %1").arg(e.value(QLatin1String("match")).toString()); return false; }
        en.group = e.value(QLatin1String("group")).toString();
        en.text = e.value(QLatin1String("text")).toString();
        en.merge = e.value(QLatin1String("merge")).toString();
        m_entries.push_back(en);
    }
    gestures.clear();
    for (const QJsonValue &v : o.value(QLatin1String("gestures")).toArray()) {
        const QJsonObject g = v.toObject();
        Gesture ge;
        for (const QJsonValue &k : g.value(QLatin1String("keys")).toArray()) ge.keys << k.toString();
        ge.text = g.value(QLatin1String("text")).toString();
        ge.group = g.value(QLatin1String("group")).toString();
        ge.touch = g.value(QLatin1String("touch")).toBool(false);
        ge.pointer = g.value(QLatin1String("pointer")).toBool(true);
        gestures.push_back(ge);
    }
    return true;
}

const Registry::Entry *Registry::find(const QString &command) const {
    for (const Entry &e : m_entries) if (e.match.match(command).hasMatch()) return &e;
    return nullptr;
}

QVector<Group> build(const ParseResult &p, const Registry &r, bool touch) {
    QMap<QString, QVector<Row>> by;
    QMap<const Registry::Entry *, int> merged;   // entry -> index of its row in its group
    QMap<const Registry::Entry *, QString> mergedGroup;
    for (const Binding &b : p.bindings) {
        if (touch) break;   // a phone has gestures, not key bindings
        const Registry::Entry *e = r.find(b.command);
        Row row;
        row.keys = prettyKeys(b.combo, b.release);
        if (!e) {
            row.text = b.command;
            row.described = false;
            by[r.otherGroup].push_back(row);
            continue;
        }
        QString text = e->text;
        const QRegularExpressionMatch m = e->match.match(b.command);
        for (int i = 1; i <= m.lastCapturedIndex(); ++i) text.replace(QLatin1Char('%') + QString::number(i), m.captured(i));
        if (!e->merge.isEmpty()) {
            if (merged.contains(e)) continue;   // the first binding stands for all
            if (!row.keys.isEmpty()) row.keys.last() = e->merge;
            text = e->text;
            for (int i = 1; i <= m.lastCapturedIndex(); ++i) text.replace(QLatin1Char('%') + QString::number(i), e->merge == QLatin1String("1 to 9") ? QStringLiteral("N") : m.captured(i));
            merged.insert(e, 1);
        }
        row.text = text;
        by[e->group].push_back(row);
    }
    for (const Gesture &g : r.gestures) {
        if (touch ? !g.touch : !g.pointer) continue;
        Row row;
        row.keys = g.keys;
        row.text = g.text;
        row.gesture = true;
        by[g.group].push_back(row);
    }
    QVector<Group> out;
    QStringList order = r.groupOrder;
    for (const QString &k : by.keys()) if (!order.contains(k) && k != r.otherGroup) order << k;
    order << r.otherGroup;
    for (const QString &t : order) {
        if (!by.contains(t) || by[t].isEmpty()) continue;
        if (touch && t == r.otherGroup) continue;
        out.push_back({ t, by[t] });
    }
    return out;
}

Group appGroup(const QString &appId, const QString &appName, const QStringList &dirs) {
    Group g;
    if (appId.isEmpty() || appId.contains(QLatin1Char('/'))) return g;
    for (const QString &d : dirs) {
        QFile f(d + QLatin1Char('/') + appId + QStringLiteral(".json"));
        if (!f.open(QIODevice::ReadOnly)) continue;
        const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
        g.title = o.value(QLatin1String("title")).toString(appName.isEmpty() ? appId : appName);
        for (const QJsonValue &v : o.value(QLatin1String("shortcuts")).toArray()) {
            const QJsonObject s = v.toObject();
            Row r;
            for (const QJsonValue &k : s.value(QLatin1String("keys")).toArray()) r.keys << k.toString();
            r.text = s.value(QLatin1String("text")).toString();
            if (!r.keys.isEmpty() && !r.text.isEmpty()) g.rows.push_back(r);
        }
        break;
    }
    return g;
}

QVector<Group> filter(const QVector<Group> &groups, const QString &query) {
    const QStringList words = query.toLower().split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
    if (words.isEmpty()) return groups;
    QVector<Group> out;
    for (const Group &g : groups) {
        Group n;
        n.title = g.title;
        for (const Row &r : g.rows) {
            const QString hay = (r.text + QLatin1Char(' ') + r.keys.join(QLatin1Char(' ')) + QLatin1Char(' ') + g.title).toLower();
            bool all = true;
            for (const QString &w : words) all = all && hay.contains(w);
            if (all) n.rows.push_back(r);
        }
        if (!n.rows.isEmpty()) out.push_back(n);
    }
    return out;
}

QVariantList toVariant(const QVector<Group> &groups) {
    QVariantList out;
    for (const Group &g : groups) {
        QVariantList rows;
        for (const Row &r : g.rows)
            rows << QVariantMap { { QStringLiteral("keys"), r.keys }, { QStringLiteral("text"), r.text },
                                  { QStringLiteral("described"), r.described }, { QStringLiteral("gesture"), r.gesture } };
        out << QVariantMap { { QStringLiteral("title"), g.title }, { QStringLiteral("rows"), rows } };
    }
    return out;
}

}  // namespace Shortcuts
