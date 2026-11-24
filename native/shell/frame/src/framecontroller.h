// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QElapsedTimer>
#include <QFileSystemWatcher>
#include <QHash>
#include <QJsonObject>
#include <QLocalServer>
#include <QObject>
#include <QSet>
#include <QTimer>
#include <atomic>
#include <functional>

#include "frameconfig.h"
#include "frameplan.h"

class Theme;
class QThread;
namespace TaosWin { class SwayTransport; }

// Follows sway (events plus a short poll, because an interactive move or resize raises no event), plans the strips,
// keeps sway's own title bar the strip's height and colours, and runs the three actions through taos_window_core.
class FrameController : public QObject {
    Q_OBJECT
public:
    struct Options {
        QString swaySocket;       // empty: $SWAYSOCK
        QString configPath;       // empty: FrameConfigFile::userPath()
        QString controlSocket;    // empty: $TAOS_FRAME_SOCK, else $XDG_RUNTIME_DIR/taos-frame.sock
        bool watchConfig = true;
        bool manageSway = true;   // set border / font / colours in sway; the tests that only plan switch it off
        int idlePollMs = 100;     // how often to look when nothing is moving
        int activePollMs = 16;    // ... while something moved in the last 400 ms
        int settleMs = 150;
        int graceMs = 1000;       // longest a new window waits to be judged (given a bar or left alone)
    };
    explicit FrameController(Theme *theme, const Options &o, QObject *parent = nullptr);
    ~FrameController() override;

    bool start(QString *error);
    const QVector<Strip> &strips() const { return m_strips; }
    const FrameTree &tree() const { return m_tree; }
    FrameConfig config() const { return m_cfg; }
    const StripMetrics &metrics() const { return m_metrics; }
    Theme *theme() const { return m_theme; }
    QJsonObject stateJson() const;

    // The three actions (button is close | minimise | full). They go through taos-window's engine, like the dock.
    bool perform(int windowId, const QString &button, QString *error = nullptr);
    // Opens the window menu for the focused framed window (the `menu` command, bound to a right click on a title bar).
    void openMenuForFocused();

    static int controlCommand(const QString &socketPath, const QString &line, QByteArray *reply, QString *error);
    static QString defaultControlSocket();

signals:
    void stripsChanged();
    void treeChanged();          // sway's window tree was read again (the corner brackets follow the focused window)
    void menuRequested(int windowId);
    void fatal(const QString &why);

private slots:
    void onTree(const QByteArray &json, bool reloaded);
    void replan();
    void applyChrome();
    void onControl();

private:
    void runCommands(const QStringList &cmds);
    void tuneFrom(const FrameTree &t);
    void startWorker();
    void applyCompositor();
    void applyTitleFormat();      // the skin's compositor keys: window corner radius, gap, wallpaper (only what changed)

    Theme *m_theme;
    Options m_o;
    FrameConfig m_cfg;
    FrameTree m_tree;
    QVector<Strip> m_strips;
    QHash<int, qint64> m_changedAt;
    QHash<int, QRect> m_lastRect;
    QHash<int, qint64> m_firstSeen;   // when each window was first in the tree
    QHash<int, bool> m_selfDrawn;     // per window: its toolkit draws its own header (read from /proc once)
    QSet<int> m_framed;
    QElapsedTimer m_clock;
    QTimer m_settle;
    QTimer m_grace;                   // looks at the tree again once the youngest window may be judged
    QByteArray m_lastJson;
    QTimer m_chromeDebounce;
    QTimer m_tuneTimer;
    // Floating windows keep their place across a skin switch: sway re-scales a floating window's position whenever the
    // workspace area changes (bar, gaps), which walks windows off the left edge over repeated switches.
    QVector<QPair<qint64, QHash<int, QRect>>> m_posHistory;
    QHash<int, QRect> m_holdPos;
    int m_holdTries = 0;
    qint64 m_skinAt = -100000;     // when the last skin switch was seen: windows are not sampled while it settles
    QTimer m_holdTimer;
    void restorePositions();
    QFileSystemWatcher m_watch;
    QLocalServer m_server;
    TaosWin::SwayTransport *m_tx = nullptr;
    QThread *m_thread = nullptr;
    std::atomic<bool> m_stop{false};
    std::atomic<int> m_pollIdle{100}, m_pollActive{16};
    std::atomic<qint64> m_lastActivityMs{0};
    int m_fontSize = 13, m_vPad = 7, m_tuneSteps = 0;
    StripMetrics m_metrics;
    qint64 m_chromeAppliedMs = 0;
    // What this daemon last told sway, so a theme switch sends only what differs. The start values are what the
    // shipped session config has (no corner radius, 8 px gaps, the session's own wallpaper).
    int m_appliedRadius = 0, m_appliedGap = 8;
    bool m_appliedBlur = false;
    QString m_titleFormat = QStringLiteral("%title");
    QSet<int> m_titleFormatted;
    QString m_appliedWallpaper, m_appliedAlign = QStringLiteral("center");
    QByteArray m_lastChrome;
    QString m_swaySock;
    std::function<void()> m_loadCfg;
    bool m_cfgRetry = false;
    qint64 m_posChangedMs = 0, m_movingSinceMs = 0;
};
