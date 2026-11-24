// SPDX-License-Identifier: AGPL-3.0-or-later
#include "framecontroller.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalSocket>
#include <QThread>
#include <cstdio>

#include "core/swayconn.h"
#include "core/windowops.h"
#include "theme.h"

using namespace TaosWin;

// What snippet 49-taos-frame.conf sets; the controller applies the same at start (a reload puts the snippet back).
static const char kFontFamily[] = "Inter";   // one family: a comma list is refused at run time
static const int kBaseFontSize = 13;   // px
static const int kBaseVPad = 7;
static const int kMaxTuneSteps = 6;

static QString hex(const QColor &c) { return c.name(QColor::HexRgb); }

// `top` painted over `base`, opaque (sway's colours have no use for a translucent hairline).
static QColor over(const QColor &base, const QColor &top)
{
    const qreal a = top.alphaF();
    return QColor::fromRgbF(base.redF() * (1 - a) + top.redF() * a, base.greenF() * (1 - a) + top.greenF() * a,
                            base.blueF() * (1 - a) + top.blueF() * a);
}

QString FrameController::defaultControlSocket()
{
    const QString env = qEnvironmentVariable("TAOS_FRAME_SOCK");
    if (!env.isEmpty())
        return env;
    const QString rt = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return (rt.isEmpty() ? QStringLiteral("/tmp") : rt) + QStringLiteral("/taos-frame.sock");
}

FrameController::FrameController(Theme *theme, const Options &o, QObject *parent)
    : QObject(parent), m_theme(theme), m_o(o)
{
    m_clock.start();
    m_pollIdle = o.idlePollMs;
    m_pollActive = o.activePollMs;
    m_settle.setSingleShot(true);
    connect(&m_settle, &QTimer::timeout, this, &FrameController::replan);
    m_grace.setSingleShot(true);
    connect(&m_grace, &QTimer::timeout, this, [this] { if (!m_lastJson.isEmpty()) onTree(m_lastJson, false); });
    m_chromeDebounce.setSingleShot(true);
    m_chromeDebounce.setInterval(30);
    connect(&m_chromeDebounce, &QTimer::timeout, this, &FrameController::applyChrome);
    if (m_theme)
        connect(m_theme, &Theme::changed, this, [this] { m_chromeDebounce.start(); replan(); });
}

FrameController::~FrameController()
{
    m_stop = true;
    if (m_thread) {
        m_thread->wait(2000);
        delete m_thread;
    }
    delete m_tx;
    QLocalServer::removeServer(m_server.fullServerName());
}

bool FrameController::start(QString *error)
{
    m_swaySock = m_o.swaySocket.isEmpty() ? SwayConnection::socketPath() : m_o.swaySocket;
    if (m_swaySock.isEmpty()) {
        if (error) *error = QStringLiteral("no sway socket ($SWAYSOCK)");
        return false;
    }
    m_tx = new SwayTransport(m_swaySock);
    QString e;
    if (!m_tx->connectNow(&e)) {
        if (error) *error = e;
        return false;
    }
    const QString cfgPath = m_o.configPath.isEmpty() ? FrameConfigFile::userPath() : m_o.configPath;
    m_loadCfg = [this, cfgPath] {
        QString err;
        FrameConfig c;
        if (!FrameConfigFile::read(cfgPath, &c, &err)) {
            // An editor that truncates and then writes shows an empty file for a moment: look once more before complaining.
            if (!m_cfgRetry) {
                m_cfgRetry = true;
                QTimer::singleShot(150, this, [this, loadCfg = m_loadCfg] { if (loadCfg) loadCfg(); });
                return;
            }
            fprintf(stderr, "taos-frame: %s (using the defaults)\n", qPrintable(err));
        } else if (!err.isEmpty()) {
            fprintf(stderr, "taos-frame: %s\n", qPrintable(err));
        }
        m_cfgRetry = false;
        if (!(c == m_cfg)) {
            m_cfg = c;
            if (m_o.manageSway && m_tx)
                runCommands(borderCommands(m_tree, m_cfg, &m_framed));
            replan();
        }
        // watch the file again: editors replace it
        if (QFile::exists(cfgPath) && !m_watch.files().contains(cfgPath)) m_watch.addPath(cfgPath);
    };
    m_cfg = FrameConfig();
    const auto loadCfg = m_loadCfg;
    loadCfg();
    if (m_o.watchConfig) {
        QDir().mkpath(QFileInfo(cfgPath).absolutePath());
        m_watch.addPath(QFileInfo(cfgPath).absolutePath());
        connect(&m_watch, &QFileSystemWatcher::fileChanged, this, loadCfg);
        connect(&m_watch, &QFileSystemWatcher::directoryChanged, this, loadCfg);
    }

    const QString sock = m_o.controlSocket.isEmpty() ? defaultControlSocket() : m_o.controlSocket;
    QLocalServer::removeServer(sock);
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server.listen(sock))
        fprintf(stderr, "taos-frame: cannot listen on %s: the right click menu will not work\n", qPrintable(sock));
    connect(&m_server, &QLocalServer::newConnection, this, &FrameController::onControl);

    if (m_o.manageSway)
        applyChrome();
    startWorker();
    return true;
}

// ----------------------------------------------------------------------------------------------- follow sway

void FrameController::startWorker()
{
    const QString path = m_swaySock;
    m_thread = QThread::create([this, path] {
        SwayConnection ev, rq;
        QString err;
        if (!ev.open(path, &err) || !rq.open(path, &err)) {
            QMetaObject::invokeMethod(this, [this, err] { emit fatal(err); }, Qt::QueuedConnection);
            return;
        }
        QByteArray reply;
        const QByteArray subs = QJsonDocument(QJsonArray{QStringLiteral("window"), QStringLiteral("workspace"),
                                                         QStringLiteral("output"), QStringLiteral("shutdown")})
                                    .toJson(QJsonDocument::Compact);
        if (!ev.request(Ipc::Subscribe, subs, &reply, &err)) {
            QMetaObject::invokeMethod(this, [this, err] { emit fatal(err); }, Qt::QueuedConnection);
            return;
        }
        QByteArray last;
        QElapsedTimer sinceChange;
        sinceChange.start();
        bool first = true;
        while (!m_stop) {
            const int wait = sinceChange.elapsed() < 400 ? m_pollActive.load() : m_pollIdle.load();
            quint32 type = 0;
            QByteArray payload;
            bool closed = false;
            bool reloaded = false;
            if (ev.next(&type, &payload, wait, &closed)) {
                if (type == Ipc::EventShutdown) { closed = true; }
                else {
                    do {
                        if (type == Ipc::EventWorkspace && payload.contains("\"reload\""))
                            reloaded = true;
                        if (type == Ipc::EventShutdown) closed = true;
                    } while (ev.next(&type, &payload, 0, &closed));
                }
            }
            if (closed) {
                QMetaObject::invokeMethod(this, [this] { emit fatal(QStringLiteral("sway went away")); }, Qt::QueuedConnection);
                return;
            }
            QByteArray tree;
            if (!rq.request(Ipc::GetTree, QByteArray(), &tree, &err)) {
                QMetaObject::invokeMethod(this, [this, err] { emit fatal(err); }, Qt::QueuedConnection);
                return;
            }
            if (first || reloaded || tree != last) {
                if (!first) sinceChange.restart();
                last = tree;
                first = false;
                QMetaObject::invokeMethod(this, [this, tree, reloaded] { onTree(tree, reloaded); }, Qt::QueuedConnection);
            }
        }
    });
    m_thread->start();
}

void FrameController::onTree(const QByteArray &json, bool reloaded)
{
    FrameTree t;
    QString err;
    m_lastJson = json;
    if (!parseFrameTree(json, &t, &err)) {
        fprintf(stderr, "taos-frame: %s\n", qPrintable(err));
        return;
    }
    // Which toolkit drew each window: read once per window from its process (GTK 4 and libadwaita draw their own header).
    QHash<int, bool> drawn;
    for (FrameWindow &w : t.windows) {
        if (!m_selfDrawn.contains(w.id)) {
            QFile f(QStringLiteral("/proc/%1/maps").arg(w.pid));
            m_selfDrawn.insert(w.id, w.pid > 0 && f.open(QIODevice::ReadOnly) && mapsShowSelfDrawingToolkit(f.readAll()));
        }
        w.selfDrawn = m_selfDrawn.value(w.id);
        drawn.insert(w.id, w.selfDrawn);
    }
    m_selfDrawn = drawn;
    const qint64 now = m_clock.elapsed();
    bool changed = false;
    QHash<int, QRect> rects;
    for (const FrameWindow &w : std::as_const(t.windows)) {
        const QRect r = w.deco.isEmpty() ? w.rect : w.deco.united(w.rect);
        rects.insert(w.id, r);
        if (m_lastRect.contains(w.id) && m_lastRect.value(w.id) != r) {
            m_changedAt.insert(w.id, now);
            changed = true;
        } else if (!m_lastRect.contains(w.id)) {
            // A new window: its strip waits for the first layout to settle.
            m_changedAt.insert(w.id, now);
        }
    }
    m_lastRect = rects;
    for (auto it = m_changedAt.begin(); it != m_changedAt.end();)
        it = rects.contains(it.key()) ? std::next(it) : m_changedAt.erase(it);
    m_tree = t;
    // The poll goes slow when no window is framed.
    bool any = false;
    for (const FrameWindow &w : std::as_const(t.windows)) any = any || m_cfg.shouldFrame(w.appId);
    m_pollIdle = any ? m_o.idlePollMs : 1000;
    Q_UNUSED(changed)

    if (m_o.manageSway) {
        if (reloaded) {
            m_fontSize = kBaseFontSize;
            m_vPad = kBaseVPad;
            m_tuneSteps = 0;
            m_lastChrome.clear();
            applyChrome();
        }
        QSet<int> ready;
        QHash<int, qint64> seen;
        qint64 youngest = -1;
        for (const FrameWindow &w : std::as_const(t.windows)) {
            seen.insert(w.id, m_firstSeen.value(w.id, now));
            // A new window shows border "none" until the client has said which decoration it wants (GTK 3 header bars
            // say client side a moment after mapping: none, then csd; foot says server side: none, then pixel). It is
            // judged once that settled, or after graceMs whatever it shows.
            if (w.border != QLatin1String("none") || now - seen.value(w.id) >= m_o.graceMs)
                ready.insert(w.id);
            else
                youngest = std::max<qint64>(youngest, m_o.graceMs - (now - seen.value(w.id)));
        }
        if (youngest >= 0)
            m_grace.start(int(youngest) + 10);
        m_firstSeen = seen;
        const QStringList cmds = borderCommands(t, m_cfg, &m_framed, &ready);
        if (!cmds.isEmpty())
            runCommands(cmds);
        tuneFrom(t);
    }
    replan();
}

void FrameController::replan()
{
    PlanInput in;
    in.buttonsRight = m_theme && m_theme->windowButtons() == QLatin1String("right");
    in.nowMs = m_clock.elapsed();
    in.settleMs = m_o.settleMs;
    in.changedAt = m_changedAt;
    const QVector<Strip> next = planStrips(m_tree, m_cfg, in);
    // Come back when the youngest "moving" window has settled.
    qint64 wait = -1;
    for (const Strip &s : next)
        if (s.reason == QLatin1String("moving"))
            wait = std::max<qint64>(wait, m_o.settleMs - (in.nowMs - m_changedAt.value(s.id)) + 5);
    if (wait >= 0)
        m_settle.start(int(wait));
    bool same = next.size() == m_strips.size();
    for (int i = 0; same && i < next.size(); ++i) {
        const Strip &a = next.at(i), &b = m_strips.at(i);
        same = a.id == b.id && a.title == b.title && a.pos == b.pos && a.margin == b.margin && a.show == b.show
               && a.output == b.output && a.right == b.right && a.focused == b.focused && a.reason == b.reason
               && a.canMinimise == b.canMinimise && a.canFull == b.canFull;
    }
    for (const Strip &n : next)
        for (const Strip &o : std::as_const(m_strips)) {
            if (o.id != n.id)
                continue;
            if (o.pos != n.pos)
                m_posChangedMs = QDateTime::currentMSecsSinceEpoch();
            if (n.reason == QLatin1String("moving") && o.reason != QLatin1String("moving"))
                m_movingSinceMs = QDateTime::currentMSecsSinceEpoch();
        }
    m_strips = next;
    if (!same)
        emit stripsChanged();
}

// ----------------------------------------------------------------------------------------------- sway chrome

void FrameController::runCommands(const QStringList &cmds)
{
    for (const QString &c : cmds) {
        QString err;
        if (!m_tx->runCommand(c, &err))
            fprintf(stderr, "taos-frame: sway refused \"%s\": %s\n", qPrintable(c), qPrintable(err));
    }
}

// sway draws the bar behind the buttons (and is what a window shows while its strip is hidden, or if this daemon
// is not running): make it the theme's surface with the theme's text, the strip's height and a hairline.
void FrameController::applyChrome()
{
    if (!m_theme || !m_tx || !m_o.manageSway)
        return;
    const QColor surface = m_theme->surface();
    const QColor hair = over(surface, m_theme->hairline());
    const QColor text = m_theme->text(), dim = m_theme->textSecondary();
    const QColor focusedBorder = over(surface, QColor(text.red(), text.green(), text.blue(), 90));
    QStringList cmds;
    // class: border  background  text  indicator  child_border
    cmds << QStringLiteral("client.focused %1 %2 %3 %1 %1").arg(hex(focusedBorder), hex(surface), hex(text));
    cmds << QStringLiteral("client.focused_inactive %1 %2 %3 %1 %1").arg(hex(hair), hex(surface), hex(dim));
    cmds << QStringLiteral("client.unfocused %1 %2 %3 %1 %1").arg(hex(hair), hex(surface), hex(dim));
    const QByteArray key = cmds.join(QLatin1Char(';')).toUtf8() + QByteArray::number(m_fontSize) + ':' + QByteArray::number(m_vPad);
    if (key == m_lastChrome)
        return;
    m_lastChrome = key;
    cmds << QStringLiteral("font pango:%1 %2px").arg(QString::fromLatin1(kFontFamily)).arg(m_fontSize);
    cmds << QStringLiteral("titlebar_padding 8 %1").arg(m_vPad);
    runCommands(cmds);
}

void FrameController::tuneFrom(const FrameTree &t)
{
    for (const FrameWindow &w : t.windows) {
        if (!m_cfg.shouldFrame(w.appId) || w.border != QLatin1String("normal") || w.deco.isEmpty() || w.fullscreen || !w.visible)
            continue;
        const TuneStep s = tuneTitleBar(w.deco.height(), m_fontSize, m_vPad);
        if (s.done) {
            m_tuneSteps = 0;
            return;
        }
        if (m_tuneSteps >= kMaxTuneSteps) {
            // Gave up: the bar is a pixel or two off; the buttons are centred on whatever height sway draws.
            return;
        }
        ++m_tuneSteps;
        m_fontSize = s.fontSize;
        m_vPad = s.vPad;
        m_lastChrome.clear();
        applyChrome();
        return;
    }
}

// ----------------------------------------------------------------------------------------------- actions

bool FrameController::perform(int id, const QString &button, QString *error)
{
    if (!m_tx) {
        if (error) *error = QStringLiteral("not connected to sway");
        return false;
    }
    for (const Strip &s : std::as_const(m_strips)) {
        if (s.id == id && ((button == QLatin1String("minimise") && !s.canMinimise) || (button == QLatin1String("full") && !s.canFull))) {
            if (error) *error = QStringLiteral("this window has no %1 button").arg(button);
            return false;
        }
    }
    WindowController c(m_tx);
    QString err;
    const QString ref = QString::number(id);
    bool ok = false;
    if (button == QLatin1String("close")) ok = c.close(ref, &err);
    else if (button == QLatin1String("minimise")) ok = c.minimise(ref, &err);
    else if (button == QLatin1String("full")) ok = c.full(ref, &err);
    else err = QStringLiteral("unknown action %1").arg(button);
    if (!ok)
        fprintf(stderr, "taos-frame: %s %d: %s\n", qPrintable(button), id, qPrintable(err));
    if (error) *error = err;
    return ok;
}

void FrameController::openMenuForFocused()
{
    for (const Strip &s : std::as_const(m_strips))
        if (s.focused) {
            emit menuRequested(s.id);
            return;
        }
}

// ----------------------------------------------------------------------------------------------- control socket

QJsonObject FrameController::stateJson() const
{
    QJsonArray arr;
    for (const Strip &s : m_strips) {
        QJsonObject o;
        o[QLatin1String("id")] = s.id;
        o[QLatin1String("app_id")] = s.appId;
        o[QLatin1String("title")] = s.title;
        o[QLatin1String("output")] = s.output;
        o[QLatin1String("x")] = s.pos.x();
        o[QLatin1String("y")] = s.pos.y();
        o[QLatin1String("margin_x")] = s.margin.x();
        o[QLatin1String("margin_y")] = s.margin.y();
        o[QLatin1String("width")] = s.size.width();
        o[QLatin1String("height")] = s.size.height();
        o[QLatin1String("right")] = s.right;
        o[QLatin1String("show")] = s.show;
        o[QLatin1String("reason")] = s.reason;
        o[QLatin1String("focused")] = s.focused;
        o[QLatin1String("can_minimise")] = s.canMinimise;
        o[QLatin1String("can_full")] = s.canFull;
        arr.append(o);
    }
    QJsonObject o;
    o[QLatin1String("strips")] = arr;
    o[QLatin1String("window_buttons")] = m_theme ? m_theme->windowButtons() : QStringLiteral("left");
    o[QLatin1String("font_size")] = m_fontSize;
    o[QLatin1String("moving_since_ms")] = double(m_movingSinceMs);   // when a strip last went into "moving"
    o[QLatin1String("pos_changed_ms")] = double(m_posChangedMs);   // when a strip last moved (ms since the epoch)
    o[QLatin1String("v_pad")] = m_vPad;
    return o;
}

void FrameController::onControl()
{
    while (QLocalSocket *c = m_server.nextPendingConnection()) {
        connect(c, &QLocalSocket::disconnected, c, &QObject::deleteLater);
        connect(c, &QLocalSocket::readyRead, this, [this, c] {
            if (!c->canReadLine())
                return;
            const QStringList w = QString::fromUtf8(c->readLine()).trimmed().split(QLatin1Char(' '), Qt::SkipEmptyParts);
            QByteArray out;
            const QString cmd = w.value(0);
            if (cmd == QLatin1String("state")) {
                out = QJsonDocument(stateJson()).toJson(QJsonDocument::Compact);
            } else if (cmd == QLatin1String("menu")) {
                openMenuForFocused();
                out = "ok";
            } else if (cmd == QLatin1String("press") && w.size() == 3) {
                QString e;
                out = perform(w.at(1).toInt(), w.at(2), &e) ? QByteArray("ok") : "error: " + e.toUtf8();
            } else {
                out = "error: unknown command";
            }
            c->write(out + "\n");
            c->flush();
            c->disconnectFromServer();
        });
    }
}

int FrameController::controlCommand(const QString &path, const QString &line, QByteArray *reply, QString *error)
{
    QLocalSocket s;
    s.connectToServer(path);
    if (!s.waitForConnected(1500)) {
        if (error) *error = QStringLiteral("taos-frame is not running (%1)").arg(path);
        return 1;
    }
    s.write(line.toUtf8() + "\n");
    s.flush();
    while (!s.canReadLine() && s.waitForReadyRead(3000)) {}
    *reply = s.readLine().trimmed();
    return reply->startsWith("error") ? 1 : 0;
}
