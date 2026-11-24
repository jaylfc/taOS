// SPDX-License-Identifier: AGPL-3.0-or-later
#include "displayconfig.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSaveFile>
#include <QRegularExpression>

QString DisplayConfigFile::defaultPath() {
    const QString env = qEnvironmentVariable("TAOS_DISPLAY_CONF");
    if (!env.isEmpty()) return env;
    QString base = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.config");
    return base + QStringLiteral("/taos/sway.d/20-display.conf");
}

QString DisplayConfigFile::quote(const QString &s) {
    QString o;
    o.reserve(s.size() + 2);
    o += QLatin1Char('"');
    for (QChar c : s) {
        if (c.unicode() < 0x20 || c.unicode() == 0x7f) { o += QLatin1Char(' '); continue; }
        if (c == QLatin1Char('"') || c == QLatin1Char('\\')) o += QLatin1Char('\\');
        o += c;
    }
    o += QLatin1Char('"');
    return o;
}

static QString scaleText(double s) {
    QString t = QString::number(s, 'f', 3);
    while (t.endsWith(QLatin1Char('0'))) t.chop(1);
    if (t.endsWith(QLatin1Char('.'))) t.chop(1);
    return t;
}

QString DisplayConfigFile::render(const QMap<QString, DisplayEntry> &all) {
    QString o;
    o += QStringLiteral("# Written by taos-display. Your display settings, one line per monitor.\n"
                        "# Change them from the display icon in the top bar; edits here are overwritten.\n");
    for (const DisplayEntry &e : all) {
        if (!e.hasMode() && !e.hasScale()) continue;
        o += QStringLiteral("output ") + quote(e.key);
        if (e.hasMode()) {
            QString hz = QString::number(e.mHz / 1000.0, 'f', 3);
            while (hz.endsWith(QLatin1Char('0'))) hz.chop(1);
            if (hz.endsWith(QLatin1Char('.'))) hz.chop(1);
            o += QStringLiteral(" mode %1x%2@%3Hz").arg(e.width).arg(e.height).arg(hz);
        }
        if (e.hasScale()) o += QStringLiteral(" scale ") + scaleText(e.scale);
        o += QLatin1Char('\n');
    }
    return o;
}

// Splits one line into words, honouring the quoting `quote` writes.
static QStringList words(const QString &line) {
    QStringList w;
    QString cur;
    bool inQ = false, have = false;
    for (int i = 0; i < line.size(); ++i) {
        const QChar c = line.at(i);
        if (inQ) {
            if (c == QLatin1Char('\\') && i + 1 < line.size()) cur += line.at(++i);
            else if (c == QLatin1Char('"')) inQ = false;
            else cur += c;
        } else if (c == QLatin1Char('"')) { inQ = true; have = true; }
        else if (c.isSpace()) { if (have || !cur.isEmpty()) { w << cur; cur.clear(); have = false; } }
        else cur += c;
    }
    if (have || !cur.isEmpty()) w << cur;
    return w;
}

QMap<QString, DisplayEntry> DisplayConfigFile::parse(const QString &text) {
    QMap<QString, DisplayEntry> m;
    for (const QString &line : text.split(QLatin1Char('\n'))) {
        if (line.trimmed().startsWith(QLatin1Char('#'))) continue;
        const QStringList w = words(line);
        if (w.size() < 3 || w.at(0) != QLatin1String("output")) continue;
        DisplayEntry e = m.value(w.at(1));
        e.key = w.at(1);
        for (int i = 2; i + 1 < w.size(); ++i) {
            if (w.at(i) == QLatin1String("mode")) {
                static const QRegularExpression re(QStringLiteral("^(\\d+)x(\\d+)@([0-9.]+)Hz$"));
                const auto mt = re.match(w.at(i + 1));
                if (mt.hasMatch()) {
                    e.width = mt.captured(1).toInt();
                    e.height = mt.captured(2).toInt();
                    e.mHz = qRound(mt.captured(3).toDouble() * 1000.0);
                }
                ++i;
            } else if (w.at(i) == QLatin1String("scale")) {
                bool ok = false;
                const double s = w.at(i + 1).toDouble(&ok);
                if (ok && s > 0) e.scale = s;
                ++i;
            }
        }
        m.insert(e.key, e);
    }
    return m;
}

QMap<QString, DisplayEntry> DisplayConfigFile::read() const {
    QFile f(m_path);
    if (!f.open(QIODevice::ReadOnly)) return {};
    return parse(QString::fromUtf8(f.readAll()));
}

bool DisplayConfigFile::write(const QMap<QString, DisplayEntry> &all, QString *error) const {
    const QByteArray want = render(all).toUtf8();
    QFile cur(m_path);
    if (cur.open(QIODevice::ReadOnly)) {
        const QByteArray have = cur.readAll();
        cur.close();
        if (have == want) return true;  // idempotent: leave the file (and its mtime) alone
    }
    if (!QDir().mkpath(QFileInfo(m_path).absolutePath())) {
        if (error) *error = QStringLiteral("cannot create %1").arg(QFileInfo(m_path).absolutePath());
        return false;
    }
    QSaveFile f(m_path);  // temp file in the same directory, renamed over the target on commit
    f.setDirectWriteFallback(false);
    if (!f.open(QIODevice::WriteOnly) || f.write(want) != want.size() || !f.commit()) {
        if (error) *error = QStringLiteral("cannot write %1: %2").arg(m_path, f.errorString());
        return false;
    }
    return true;
}

bool DisplayConfigFile::upsert(const DisplayEntry &patch, QString *error) const {
    QMap<QString, DisplayEntry> all = read();
    DisplayEntry e = all.value(patch.key);
    e.key = patch.key;
    if (patch.hasMode()) { e.width = patch.width; e.height = patch.height; e.mHz = patch.mHz; }
    if (patch.hasScale()) e.scale = patch.scale;
    all.insert(e.key, e);
    return write(all, error);
}
