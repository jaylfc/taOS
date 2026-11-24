// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMap>
#include <QString>

// ~/.config/taos/sway.d/20-display.conf: the person's display settings, one
// `output "<make model serial>" mode WxH@RHz scale S` line per monitor, keyed
// by what the monitor is and not by the connector it sits on. Generated; the
// whole file is rewritten atomically (temp file, fsync, rename) and only when
// its content really changes. Entries of monitors that are not plugged in are
// kept. /etc is never touched.
struct DisplayEntry {
    QString key;
    int width = 0, height = 0, mHz = 0;  // 0: no mode saved
    double scale = 0;                    // 0: no scale saved
    bool hasMode() const { return width > 0 && height > 0 && mHz > 0; }
    bool hasScale() const { return scale > 0; }
};

class DisplayConfigFile {
public:
    explicit DisplayConfigFile(const QString &path = defaultPath()) : m_path(path) {}
    // $TAOS_DISPLAY_CONF, else $XDG_CONFIG_HOME/taos/sway.d/20-display.conf, else ~/.config/...
    static QString defaultPath();
    QString path() const { return m_path; }

    QMap<QString, DisplayEntry> read() const;
    // Merges `patch` into the entry of its key (only the fields it has) and writes.
    // Returns false and sets *error when the file cannot be written.
    bool upsert(const DisplayEntry &patch, QString *error = nullptr) const;
    bool write(const QMap<QString, DisplayEntry> &all, QString *error = nullptr) const;

    static QString render(const QMap<QString, DisplayEntry> &all);
    static QMap<QString, DisplayEntry> parse(const QString &text);
    // sway config quoting: double quotes, backslash before " and \, control characters to spaces.
    static QString quote(const QString &s);

private:
    QString m_path;
};
