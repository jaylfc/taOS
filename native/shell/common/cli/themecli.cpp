// SPDX-License-Identifier: AGPL-3.0-or-later
#include "themecli.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include "appearance.h"
#include "themecatalog.h"

static int usage(QTextStream &err) {
    err << "usage: taos-theme list [--json]\n"
           "       taos-theme get [--json]\n"
           "       taos-theme set <theme> [--mode dark|light|auto]\n"
           "       taos-theme set --mode dark|light|auto\n"
           "       taos-theme export foot|button-layout      (for other apps: foot's [csd] section, GTK's button-layout string)\n"
           "       taos-theme set-buttons left|right|theme   (which side the window buttons are on; theme = the theme's own)\n"
           "       taos-theme validate <theme.json | theme folder>   (check a theme file; changes nothing)\n"
           "       taos-theme options <theme>                        (the choices a theme offers and which is picked)\n"
           "       taos-theme option <theme> <key> <value>           (pick one; live, and saved per person)\n"
           "exit status: 0 done / valid, 1 refused (unknown theme, invalid theme, cannot save), 2 wrong usage, 3 a file given to validate cannot be read\n";
    return 2;
}

static QJsonArray swatchArray(const QVector<QColor> &v) {
    QJsonArray a;
    for (const QColor &c : v) a << toCssColor(c);
    return a;
}

int runThemeCli(const QStringList &argsIn, QTextStream &out, QTextStream &err) {
    QStringList args = argsIn;
    if (args.isEmpty()) return usage(err);
    const QString cmd = args.takeFirst();
    bool json = false;
    QString mode;
    QStringList pos;
    for (int i = 0; i < args.size(); ++i) {
        if (args[i] == QLatin1String("--json")) json = true;
        else if (args[i] == QLatin1String("--mode") && i + 1 < args.size()) mode = args[++i];
        else if (args[i].startsWith(QLatin1String("--mode="))) mode = args[i].mid(7);
        else if (args[i].startsWith(QLatin1String("--"))) { err << "taos-theme: unknown option " << args[i] << "\n"; return usage(err); }
        else pos << args[i];
    }
    ThemeCatalog cat;
    cat.scan(ThemeCatalog::standardDirs(true));
    Appearance cur;
    AppearanceFile::read(AppearanceFile::userPath(), &cur);
    const ThemeDef *shown = cat.resolve(cur.theme);

    if (cmd == QLatin1String("list")) {
        if (!pos.isEmpty() || !mode.isEmpty()) return usage(err);
        if (json) {
            QJsonArray ts;
            for (const ThemeDef &d : cat.themes()) {
                QJsonObject o;
                o.insert(QStringLiteral("id"), d.id);
                o.insert(QStringLiteral("name"), d.name);
                o.insert(QStringLiteral("description"), d.description);
                o.insert(QStringLiteral("source"), d.builtin ? QStringLiteral("built-in") : d.source);
                o.insert(QStringLiteral("variants"), QJsonArray::fromStringList(d.variants));
                QJsonObject sw;
                for (auto it = d.swatches.cbegin(); it != d.swatches.cend(); ++it) sw.insert(it.key(), swatchArray(it.value()));
                o.insert(QStringLiteral("swatches"), sw);
                o.insert(QStringLiteral("current"), shown && shown->id == d.id);
                ts << o;
            }
            QJsonArray rf;
            for (const auto &r : cat.refused()) {
                QJsonObject o;
                o.insert(QStringLiteral("id"), r.id);
                o.insert(QStringLiteral("source"), r.source);
                o.insert(QStringLiteral("message"), r.message);
                rf << o;
            }
            QJsonObject root;
            root.insert(QStringLiteral("current"), QJsonObject{{QStringLiteral("theme"), shown ? shown->id : cur.theme}, {QStringLiteral("mode"), cur.mode}});
            root.insert(QStringLiteral("themes"), ts);
            root.insert(QStringLiteral("refused"), rf);
            out << QJsonDocument(root).toJson(QJsonDocument::Indented);
            return 0;
        }
        for (const ThemeDef &d : cat.themes()) {
            out << ((shown && shown->id == d.id) ? "* " : "  ") << d.id.leftJustified(16) << d.name.leftJustified(18) << d.description << "\n";
        }
        for (const auto &r : cat.refused()) err << "taos-theme: refused " << r.id << " (" << r.source << "): " << r.message << "\n";
        return 0;
    }
    if (cmd == QLatin1String("validate")) {
        if (pos.size() != 1 || json || !mode.isEmpty()) return usage(err);
        QString path = pos[0];
        if (QFileInfo(path).isDir()) path = QDir(path).filePath(QStringLiteral("theme.json"));
        if (!QFileInfo(path).isFile() || !QFileInfo(path).isReadable()) {
            err << "taos-theme: " << path << ": the file cannot be read\n";
            return 3;
        }
        // a theme.json is checked against its folder name, like the picker does; any other file name is not
        const QFileInfo fi(path);
        const QString expected = fi.fileName() == QLatin1String("theme.json") ? fi.absoluteDir().dirName() : QString();
        const ThemeParse p = parseThemeFile(path, expected);
        if (!p.ok) {
            for (const QString &e : p.errors) err << path << ": " << e << "\n";
            err << "taos-theme: " << path << " is not a valid theme (" << p.errors.size() << (p.errors.size() == 1 ? " problem)\n" : " problems)\n");
            return 1;
        }
        const ThemeDef &d = p.def;
        out << "OK " << d.id << " (" << d.name << "), variants " << d.variants.join(QLatin1Char('+'))
            << "; dock mode " << d.layout.dock << ", top bar " << (d.layout.topBar ? "yes" : "no")
            << ", window buttons " << d.windowButtons << " " << d.skin.buttonShape << "\n";
        return 0;
    }
    if (cmd == QLatin1String("options")) {
        if (pos.size() != 1 || !mode.isEmpty()) return usage(err);
        const ThemeDef *d = cat.find(pos[0]);
        if (!d) { err << "taos-theme: no theme called '" << pos[0] << "'\n"; return 1; }
        const QMap<QString, QString> chosen = cur.options.value(d->id);
        QJsonArray arr;
        for (const ThemeOption &o : d->options) {
            const QString now = o.find(chosen.value(o.key)) ? chosen.value(o.key) : o.defaultChoice;
            if (json) {
                QJsonArray cs;
                for (const ThemeChoice &c : o.choices)
                    cs << QJsonObject{{QStringLiteral("id"), c.id}, {QStringLiteral("label"), c.label}, {QStringLiteral("available"), c.available}, {QStringLiteral("note"), c.note}};
                arr << QJsonObject{{QStringLiteral("key"), o.key}, {QStringLiteral("label"), o.label}, {QStringLiteral("current"), now}, {QStringLiteral("choices"), cs}};
                continue;
            }
            out << o.key.leftJustified(10) << o.label << ": ";
            for (const ThemeChoice &c : o.choices)
                out << (c.id == now ? "[" : "") << c.id << (c.available ? "" : " (not available yet)") << (c.id == now ? "]" : "") << " ";
            out << "\n";
        }
        if (json) out << QJsonDocument(QJsonObject{{QStringLiteral("theme"), d->id}, {QStringLiteral("options"), arr}}).toJson(QJsonDocument::Indented);
        else if (d->options.isEmpty()) out << d->name << " has no options.\n";
        return 0;
    }
    if (cmd == QLatin1String("option")) {
        if (pos.size() != 3 || json || !mode.isEmpty()) return usage(err);
        const ThemeDef *d = cat.find(pos[0]);
        if (!d) { err << "taos-theme: no theme called '" << pos[0] << "'\n"; return 1; }
        const ThemeOption *opt = nullptr;
        for (const ThemeOption &o : d->options) if (o.key == pos[1]) opt = &o;
        if (!opt) {
            err << "taos-theme: " << d->name << " has no option '" << pos[1] << "'. It offers:";
            for (const ThemeOption &o : d->options) err << " " << o.key;
            err << "\n";
            return 1;
        }
        const ThemeChoice *ch = opt->find(pos[2]);
        if (!ch) {
            err << "taos-theme: '" << pos[2] << "' is not a choice of " << opt->label << ". Choices:";
            for (const ThemeChoice &c : opt->choices) err << " " << c.id;
            err << "\n";
            return 1;
        }
        Appearance next = cur;
        next.options[d->id].insert(opt->key, ch->id);
        QString e;
        if (!AppearanceFile::write(AppearanceFile::userPath(), next, &e)) { err << "taos-theme: " << e << "\n"; return 1; }
        out << d->name << ": " << opt->label << " = " << ch->label << "\n";
        if (!ch->available)
            out << "Saved, but not available on this system yet" << (ch->note.isEmpty() ? QString() : QStringLiteral(" (") + ch->note + QLatin1Char(')'))
                << ": the theme keeps showing " << opt->find(opt->defaultChoice)->label << ".\n";
        return 0;
    }
    if (cmd == QLatin1String("get")) {
        if (!pos.isEmpty() || !mode.isEmpty()) return usage(err);
        const QString v = AppearanceFile::resolveVariant(cur.mode, QDateTime::currentDateTime());
        const QString id = shown ? shown->id : cur.theme;
        const QString side = validButtonSide(cur.windowButtons) ? cur.windowButtons : (shown ? shown->windowButtons : QStringLiteral("left"));
        if (json) {
            QJsonObject o{{QStringLiteral("theme"), id}, {QStringLiteral("mode"), cur.mode}, {QStringLiteral("variant"), v},
                          {QStringLiteral("name"), shown ? shown->name : id},
                          {QStringLiteral("windowButtons"), side},
                          {QStringLiteral("windowButtonsSetting"), cur.windowButtons.isEmpty() ? QStringLiteral("theme") : cur.windowButtons}};
            out << QJsonDocument(o).toJson(QJsonDocument::Indented);
        } else {
            out << id << " " << cur.mode << "\n";
        }
        return 0;
    }
    if (cmd == QLatin1String("export")) {
        if (pos.size() != 1 || json || !mode.isEmpty()) return usage(err);
        const QString side = validButtonSide(cur.windowButtons) ? cur.windowButtons : (shown ? shown->windowButtons : QStringLiteral("left"));
        if (pos[0] == QLatin1String("button-layout")) {
            // GTK / GNOME: the buttons on the left side of the colon are drawn at the left.
            out << (side == QLatin1String("right") ? ":minimize,maximize,close" : "close,minimize,maximize:") << "\n";
            return 0;
        }
        if (pos[0] == QLatin1String("foot")) {
            if (!shown) { err << "taos-theme: no theme is usable\n"; return 1; }
            const QString v = AppearanceFile::resolveVariant(cur.mode, QDateTime::currentDateTime());
            const TokenSet &c = shown->colors.contains(v) ? shown->colors[v] : shown->colors.begin().value();
            const QColor border = QColor::fromRgbF(
                c[ThemeTokens::Surface].redF() * 0.9 + c[ThemeTokens::Text].redF() * 0.1,
                c[ThemeTokens::Surface].greenF() * 0.9 + c[ThemeTokens::Text].greenF() * 0.1,
                c[ThemeTokens::Surface].blueF() * 0.9 + c[ThemeTokens::Text].blueF() * 0.1);
            auto hex = [](const QColor &q) { return q.name(QColor::HexRgb).mid(1); };   // RRGGBB, opaque
            // preferred=server: foot asks the compositor for its title bar, and taos-frame draws the taOS window
            // buttons (close, minimise, full size, on the theme's side) on it, as for every other window. foot's own
            // header only ever offered a close button and could not be placed on the left. The [csd] colours below
            // are what foot would paint if a compositor refused server side decoration (then foot draws its own).
            out << "# Written by taos-theme export foot. foot asks the compositor for the title bar (taos-frame draws the window buttons on it).\n"
                   "[csd]\npreferred=server\nsize=32\nbutton-width=32\nborder-width=1\n"
                << "color=ff" << hex(c[ThemeTokens::Surface]) << "\n"
                << "border-color=ff" << hex(border) << "\n"
                // button-color is the SYMBOL colour and the per-button colours are the backgrounds foot
                // paints on hover. The symbol must contrast with the title bar at rest (it used to be the
                // surface colour, so the buttons were invisible until hovered).
                << "button-color=ff" << hex(c[ThemeTokens::Text]) << "\n"
                << "button-minimize-color=ff" << hex(border) << "\n"
                << "button-maximize-color=ff" << hex(border) << "\n"
                << "button-close-color=ff" << hex(c[ThemeTokens::Danger]) << "\n";
            return 0;
        }
        return usage(err);
    }
    if (cmd == QLatin1String("set-buttons")) {
        if (pos.size() != 1 || json || !mode.isEmpty()) return usage(err);
        const QString choice = pos[0];
        if (choice != QLatin1String("theme") && !validButtonSide(choice)) {
            err << "taos-theme: set-buttons takes left, right or theme\n";
            return 2;
        }
        Appearance next = cur;
        if (shown) next.theme = shown->id;
        next.windowButtons = choice == QLatin1String("theme") ? QString() : choice;
        QString e;
        if (!AppearanceFile::write(AppearanceFile::userPath(), next, &e)) {
            err << "taos-theme: " << e << "\n";
            return 1;
        }
        const QString effective = validButtonSide(next.windowButtons) ? next.windowButtons : (shown ? shown->windowButtons : QStringLiteral("left"));
        out << "Window buttons: " << effective << (choice == QLatin1String("theme") ? " (from the theme)" : "") << "\n";
        return 0;
    }
    if (cmd == QLatin1String("set")) {
        if (pos.size() > 1 || (pos.isEmpty() && mode.isEmpty()) || json) return usage(err);
        Appearance next = cur;
        if (shown) next.theme = shown->id;
        if (!pos.isEmpty()) {
            if (!cat.find(pos[0])) {
                err << "taos-theme: no theme called '" << pos[0] << "'. Installed:";
                for (const ThemeDef &d : cat.themes()) err << " " << d.id;
                err << "\n";
                for (const auto &r : cat.refused())
                    if (r.id == pos[0]) err << "taos-theme: " << r.id << " was refused: " << r.message << "\n";
                return 1;
            }
            next.theme = pos[0];
        }
        if (!mode.isEmpty()) {
            if (!AppearanceFile::validMode(mode)) { err << "taos-theme: mode must be dark, light or auto\n"; return 2; }
            next.mode = mode;
        }
        QString e;
        if (!AppearanceFile::write(AppearanceFile::userPath(), next, &e)) {
            err << "taos-theme: " << e << "\n";
            return 1;
        }
        const ThemeDef *d = cat.find(next.theme);
        out << "Theme: " << (d ? d->name : next.theme) << ", " << next.mode << "\n";
        return 0;
    }
    return usage(err);
}
