#pragma once
#include <QTimer>

#include "swayipc.h"
#include "windowengine.h"

// FALLBACK window engine: talks to sway directly. Marked for replacement by an adapter over
// taos_window_core (see windowengine.h). Behaviour follows the contract: a minimised window is
// moved to the scratchpad and carries the mark taos_min_<conid>; nothing is kept in memory.
class SwayEngine : public WindowEngine {
    Q_OBJECT
public:
    explicit SwayEngine(const QString &socketPath = QString(), QObject *parent = nullptr);
    QString name() const override { return QStringLiteral("sway-fallback"); }
    void start() override;
    bool connected() const { return m_req.isConnected(); }
    void refresh() override;
    QVector<WindowInfo> windows() const override { return m_windows; }
    void close(int id) override;
    void minimise(int id) override;
    void restore(int id) override;
    void toggleMinimise(int id) override;
    void focus(int id) override;
    void full(int id) override;
    QString focusedWorkspace() const override { return m_focusedWs; }

    // The sway commands for each operation (pure; the tests check them).
    static QString minimiseCommand(int id, bool floating);
    static QString restoreCommand(int id, bool wasFloating);

private:
    const WindowInfo *find(int id) const;
    void run(const QString &cmd);
    void scheduleRefresh();
    QString m_path;
    SwayIpc m_req, m_ev;
    QTimer m_debounce, m_reconnect;
    QVector<WindowInfo> m_windows;
    void refreshWorkspace();
    QString m_focusedWs;
    bool m_have = false;
};
