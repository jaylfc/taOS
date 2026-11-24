#include "providers_more.h"

#include <QDir>
#include <QMimeDatabase>
#include <QMimeType>
#include <algorithm>

#include <thread>
#include "fuzzy.h"

namespace taos::search {

// ---------------------------------------------------------------- files

void FilesProvider::prepare() {
    if (!m_started) { m_started = true; m_index->loadCache(); }
    if (!m_index->stale()) return;
    std::shared_ptr<FileIndex> idx = m_index;
    // the walk runs on its own thread, never on the launcher's: results come from the last snapshot
    std::thread([idx] {
        if (!idx->tryBegin()) return;
        idx->rebuild(CancelToken());
        idx->end();
    }).detach();
}

static bool subsequence(const QString &needle, const QString &hay) {
    int j = 0;
    for (int i = 0; i < hay.size() && j < needle.size(); ++i) if (hay[i] == needle[j]) ++j;
    return j == needle.size();
}

static QString tilde(const QString &p) {
    const QString h = QDir::homePath();
    return p.startsWith(h) ? QStringLiteral("~") + p.mid(h.size()) : p;
}

QList<Result> FilesProvider::query(const QString &text, const CancelToken &cancel) {
    const QString q = text.trimmed();
    if (q.size() < 2) return {};
    const auto snap = m_index->snapshot();
    QString lq = q.toLower();
    lq.remove(QLatin1Char(' '));
    QList<Result> out;
    QList<QPair<double, int>> scored;
    int n = 0;
    for (int i = 0; i < snap->size(); ++i) {
        if ((++n & 0x3FF) == 0 && cancel.cancelled()) return {};
        const FileEntry &e = (*snap)[i];
        if (!subsequence(lq, e.lname)) continue;   // cheap prefilter before the real scoring
        const double s = fuzzyScore(q, e.name);
        if (s < 0.2) continue;
        scored.append({ s - (e.dir ? 0.0 : 0.0), i });
    }
    std::sort(scored.begin(), scored.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    static QMimeDatabase mime;
    for (int k = 0; k < scored.size() && k < 8; ++k) {
        const FileEntry &e = (*snap)[scored[k].second];
        Result r;
        r.id = QStringLiteral("file:") + e.path;
        r.provider = id();
        r.kind = QStringLiteral("file");
        r.title = e.name;
        r.subtitle = tilde(QFileInfo(e.path).absolutePath());
        r.icon = e.dir ? QStringLiteral("folder") : mime.mimeTypeForFile(e.name, QMimeDatabase::MatchExtension).iconName();
        r.score = scored[k].first;
        r.actions << Action{QStringLiteral("open"), e.dir ? QStringLiteral("Open folder") : QStringLiteral("Open"), QStringLiteral("open"), {}, e.path, false, false, QStringLiteral("Open")};
        r.actions << Action{QStringLiteral("reveal"), QStringLiteral("Show in folder"), QStringLiteral("open"), {}, QFileInfo(e.path).absolutePath()};
        r.actions << Action{QStringLiteral("copy-path"), QStringLiteral("Copy path"), QStringLiteral("copy"), {}, e.path};
        out << r;
    }
    return out;
}

// ------------------------------------------------------------- settings

namespace {
// `section` is the Settings app section the page opens (`taos-settings --section <id>`); "" where the app has no such page yet.
struct Page { const char *id, *title, *sub; const char *kw; const char *section; };
const Page kPages[] = {
    {"appearance", "Appearance", "Theme, light and dark, accent", "theme dark light colour color look", "appearance"},
    {"display", "Displays", "Resolution, scale and arrangement", "screen monitor scale resolution refresh", "display"},
    {"dock", "Dock", "Position, size and auto-hide", "dock bar taskbar", "dock"},
    {"desktop", "Desktop and windows", "Window style, shortcuts", "tiling floating window mode keys shortcuts hotkeys", "desktop"},
    {"wallpaper", "Wallpaper", "Desktop background", "background picture", ""},
    {"sound", "Sound", "Volume, input and output devices", "audio speaker microphone volume", "sound"},
    {"network", "Network", "Wi-Fi and wired connections", "wifi wi-fi ethernet internet vpn", "network"},
    {"bluetooth", "Bluetooth", "Pair and manage devices", "headphones pair", "network"},
    {"power", "Power and battery", "Sleep, screen off, power profile", "battery sleep suspend", ""},
    {"keyboard", "Keyboard", "Layouts and shortcuts", "shortcuts layout language typing", "desktop"},
    {"pointer", "Mouse and touchpad", "Speed, scrolling, tap to click", "mouse trackpad scroll", ""},
    {"accounts", "Accounts", "People who use this computer", "users password login", ""},
    {"privacy", "Privacy and security", "Permissions and locking", "permissions camera microphone lock", ""},
    {"datetime", "Date and time", "Clock, time zone", "clock timezone", ""},
    {"language", "Language and region", "Language, formats", "locale region", ""},
    {"notifications", "Notifications", "What can interrupt you", "alerts do not disturb", ""},
    {"agents", "Agents", "Your taOS agents and what they can do", "ai assistant", ""},
    {"models", "Models", "Downloaded and connected models", "ai llm", ""},
    {"storage", "Storage", "Disks and space", "disk drive space", ""},
    {"updates", "Updates", "System and app updates", "upgrade version", ""},
    {"about", "About this computer", "Name, hardware, version", "info system", "about"},
};
}  // namespace

QList<Result> SettingsProvider::query(const QString &text, const CancelToken &cancel) {
    const QString q = text.trimmed();
    QList<Result> out;
    if (q.isEmpty()) return out;
    const bool app = m_exists(QStringLiteral("taos-settings"));
    for (const Page &p : kPages) {
        if (cancel.cancelled()) return {};
        double s = fuzzyScore(q, QString::fromUtf8(p.title));
        s = qMax(s, fuzzyScore(q, QString::fromUtf8(p.kw)) * 0.65);
        if (s < 0.2) continue;
        Result r;
        r.id = QStringLiteral("setting:") + QString::fromUtf8(p.id);
        r.provider = id();
        r.kind = QStringLiteral("setting");
        r.title = QString::fromUtf8(p.title);
        r.icon = QStringLiteral("preferences-system");
        r.score = s;
        Action a;
        a.id = QStringLiteral("open");
        if (app && *p.section) {
            r.subtitle = QString::fromUtf8(p.sub);
            a.label = QStringLiteral("Open in Settings");
            a.hint = QStringLiteral("Open");
            a.verb = QStringLiteral("exec");
            a.args = { QStringLiteral("taos-settings"), QStringLiteral("--section"), QString::fromUtf8(p.section) };
        } else if (app) {
            r.subtitle = QString::fromUtf8(p.sub) + QStringLiteral("  ·  not in Settings yet");
            a.label = QStringLiteral("Settings has no page for this yet");
            a.hint = QStringLiteral("Not yet");
            a.verb = QStringLiteral("stub");
            a.stub = true;
        } else {
            r.subtitle = QString::fromUtf8(p.sub) + QStringLiteral("  ·  Settings app not available yet");
            a.label = QStringLiteral("The Settings app is not available yet");
            a.hint = QStringLiteral("Not yet");
            a.verb = QStringLiteral("stub");
            a.stub = true;
        }
        r.actions << a;
        out << r;
    }
    std::sort(out.begin(), out.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
    return out;
}

// ------------------------------------------------------------------ ask

QStringList AskProvider::chatDesktopIds() {
    return { QStringLiteral("org.taos.Messages.desktop"), QStringLiteral("taos-messages.desktop"), QStringLiteral("org.taos.Chat.desktop"),
             QStringLiteral("taos-chat.desktop"), QStringLiteral("org.taos.Agents.desktop") };
}

QList<Result> AskProvider::query(const QString &text, const CancelToken &) {
    const QString q = text.trimmed();
    if (q.isEmpty()) return {};
    Result r;
    r.id = QStringLiteral("ask:query");
    r.provider = id();
    r.kind = QStringLiteral("ask");
    r.title = QStringLiteral("Ask taOS: ") + q;
    r.icon = QStringLiteral("dialog-question");
    r.score = 0.01;
    Action a;
    a.id = QStringLiteral("ask");
    QStringList cmd = m_cfg->askCommand();
    if (!cmd.isEmpty()) {
        for (QString &w : cmd) w.replace(QStringLiteral("{query}"), q);
        r.subtitle = QStringLiteral("Send to your agent");
        a.label = QStringLiteral("Ask");
        a.verb = QStringLiteral("exec");
        a.args = cmd;
    } else {
        // STUB: no controller call. When a chat app is installed it is opened and the question is copied.
        const QList<DesktopEntry> entries = m_entries ? m_entries() : QList<DesktopEntry>();
        const DesktopEntry *chat = nullptr;
        for (const QString &id : chatDesktopIds()) {
            for (const DesktopEntry &e : entries) if (e.id == id) { chat = &e; break; }
            if (chat) break;
        }
        if (chat) {
            r.subtitle = QStringLiteral("Opens %1 with your question copied").arg(chat->name);
            a.label = QStringLiteral("Open %1").arg(chat->name);
            a.hint = QStringLiteral("Ask");
            a.verb = QStringLiteral("ask-open");
            a.args = execToArgv(chat->exec, *chat);
            a.data = q;
        } else {
            r.subtitle = QStringLiteral("Agent chat is not set up yet");
            a.label = QStringLiteral("Agent chat is not set up yet");
            a.hint = QStringLiteral("Not yet");
            a.verb = QStringLiteral("stub");
            a.stub = true;
        }
    }
    r.actions << a;
    return { r };
}

// -------------------------------------------------------------- mentions

QString MentionProvider::handleOf(const QString &name) {
    QString h;
    for (const QChar c : name.toLower()) {
        if (c.isLetterOrNumber() || c == QLatin1Char('_') || c == QLatin1Char('-')) h += c;
        else if (c.isSpace() && !h.endsWith(QLatin1Char('-'))) h += QLatin1Char('-');
    }
    while (h.endsWith(QLatin1Char('-'))) h.chop(1);
    return h;
}

QList<Result> MentionProvider::query(const QString &textIn, const CancelToken &cancel) {
    const QString text = textIn;
    if (!text.startsWith(QLatin1Char('@'))) return {};
    const QList<Agent> agents = m_dir->agents();
    if (agents.isEmpty()) {
        Result r;
        r.id = QStringLiteral("mention:unavailable");
        r.provider = id();
        r.kind = QStringLiteral("mention");
        r.title = QStringLiteral("Mention an agent");
        r.subtitle = m_dir->unavailableReason().isEmpty() ? QStringLiteral("No agents yet") : m_dir->unavailableReason();
        r.icon = QStringLiteral("system-users");
        r.score = 1.0;
        Action a;
        a.id = QStringLiteral("stub");
        a.label = r.subtitle;
        a.hint = QStringLiteral("Not yet");
        a.verb = QStringLiteral("stub");
        a.stub = true;
        r.actions << a;
        return { r };
    }
    // leading mentions: "@a @b text"
    QStringList tokens;
    int pos = 0;
    bool partial = false;   // the last token is still being typed (no space after it)
    while (pos < text.size() && text[pos] == QLatin1Char('@')) {
        int e = pos + 1;
        while (e < text.size() && !text[e].isSpace()) ++e;
        tokens << text.mid(pos + 1, e - pos - 1);
        if (e >= text.size()) { partial = true; pos = e; break; }
        while (e < text.size() && text[e].isSpace()) ++e;
        pos = e;
    }
    const QString message = partial ? QString() : text.mid(pos).trimmed();
    QList<Result> out;
    const auto find = [&](const QString &h) -> const Agent * {
        for (const Agent &a : agents) if (handleOf(a.name) == h.toLower() || a.name.compare(h, Qt::CaseInsensitive) == 0) return &a;
        return nullptr;
    };
    const auto statusText = [](const Agent &a) { return a.status.isEmpty() ? QStringLiteral("Agent") : QStringLiteral("Agent  ·  ") + a.status; };

    if (partial) {
        // autocomplete for the token being typed; earlier tokens stay in the text
        QString prefix;
        for (int i = 0; i + 1 < tokens.size(); ++i) prefix += QLatin1Char('@') + tokens[i] + QLatin1Char(' ');
        const QString typed = tokens.last();
        for (const Agent &a : agents) {
            if (cancel.cancelled()) return {};
            const QString h = handleOf(a.name);
            double s = typed.isEmpty() ? 0.5 : qMax(fuzzyScore(typed, h), fuzzyScore(typed, a.name));
            if (s <= 0.1) continue;
            if (tokens.mid(0, tokens.size() - 1).contains(h, Qt::CaseInsensitive)) continue;   // already mentioned
            if (a.status == QLatin1String("online")) s += 0.02;
            Result r;
            r.id = QStringLiteral("mention:") + a.id;
            r.provider = id();
            r.kind = QStringLiteral("mention");
            r.title = QStringLiteral("@") + h;
            r.subtitle = statusText(a) + (a.name != h ? QStringLiteral("  ·  ") + a.name : QString());
            r.icon = QString();
            r.score = s;
            r.actions << Action{QStringLiteral("complete"), QStringLiteral("Complete"), QStringLiteral("complete"), {}, prefix + QLatin1Char('@') + h + QLatin1Char(' '), false, false, QStringLiteral("Complete")};
            out << r;
        }
        std::sort(out.begin(), out.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
        return out;
    }

    // complete mentions followed by (maybe) a message
    QList<const Agent *> targets;
    QStringList unknown;
    for (const QString &t : tokens) {
        const Agent *a = find(t);
        if (!a) { unknown << t; continue; }
        bool dup = false;
        for (const Agent *x : targets) if (x->id == a->id) dup = true;
        if (!dup) targets << a;
    }
    Result r;
    r.provider = id();
    r.kind = QStringLiteral("mention");
    r.icon = QString();
    r.score = 1.0;
    if (targets.isEmpty()) {
        r.id = QStringLiteral("mention:unknown");
        r.title = QStringLiteral("No agent called @%1").arg(unknown.value(0));
        r.subtitle = QStringLiteral("Type @ to see your agents");
        Action a; a.id = QStringLiteral("stub"); a.label = r.title; a.hint = QStringLiteral("Not found"); a.verb = QStringLiteral("stub"); a.stub = true;
        r.actions << a;
        return { r };
    }
    const bool sendAll = m_multi && targets.size() > 1;
    QStringList ids, names;
    for (int i = 0; i < (sendAll ? targets.size() : 1); ++i) { ids << targets[i]->id; names << QStringLiteral("@") + handleOf(targets[i]->name); }
    r.id = QStringLiteral("mention:send:") + ids.join(QLatin1Char(','));
    r.title = QStringLiteral("Send to ") + names.join(QStringLiteral(", "));
    QString sub = message.isEmpty() ? QStringLiteral("Type your message") : message;
    if (targets.size() > 1 && !sendAll) sub += QStringLiteral("  ·  Chat takes one agent per message: only %1 gets it").arg(names.first());
    if (!unknown.isEmpty()) sub += QStringLiteral("  ·  No agent called @") + unknown.join(QStringLiteral(", @"));
    r.subtitle = sub;
    if (message.isEmpty()) {
        Action a; a.id = QStringLiteral("stub"); a.label = QStringLiteral("Type a message after the name"); a.hint = QStringLiteral("Type a message"); a.verb = QStringLiteral("stub"); a.stub = true;
        r.actions << a;
        return { r };
    }
    const bool open = m_cfg->mentionOpensChat();
    Action send{QStringLiteral("send"), QStringLiteral("Send only"), QStringLiteral("send-mention"), ids, message, false, false, QStringLiteral("Send")};
    Action sendOpen{QStringLiteral("send-open"), QStringLiteral("Send and open chat"), QStringLiteral("send-mention"), ids, message, false, false, QStringLiteral("Send and open")};
    if (open) r.actions << sendOpen << send; else r.actions << send << sendOpen;
    return { r };
}

}  // namespace taos::search
