// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QSize>
#include <QTimer>
#include <QVariantList>

#include "desktopentry.h"
#include "dockconfig.h"
#include "desktopmode.h"
#include "dockmodel.h"
#include "layout.h"
#include "taskbarmodel.h"
#include "windowengine.h"

// The dock's brain and its QML face: owns the setting, the application index, the window engine and
// the model, turns clicks into engine calls, and computes the layout the surface follows.
class DockController : public QObject {
    Q_OBJECT
    Q_PROPERTY(DockModel *model READ model CONSTANT)
    Q_PROPERTY(TaskbarModel *taskbar READ taskbar CONSTANT)
    Q_PROPERTY(QString position READ position NOTIFY layoutChanged)
    Q_PROPERTY(bool vertical READ vertical NOTIFY layoutChanged)
    Q_PROPERTY(bool attached READ attached NOTIFY layoutChanged)
    Q_PROPERTY(bool autohide READ autohide NOTIFY layoutChanged)
    Q_PROPERTY(bool magnify READ magnify NOTIFY layoutChanged)
    Q_PROPERTY(int iconSize READ iconSize NOTIFY layoutChanged)
    Q_PROPERTY(int cell READ cell NOTIFY layoutChanged)
    Q_PROPERTY(int separatorWidth READ separatorWidth NOTIFY layoutChanged)
    Q_PROPERTY(int padding READ padding NOTIFY layoutChanged)
    Q_PROPERTY(int radius READ radius NOTIFY layoutChanged)
    Q_PROPERTY(QRect stripRect READ stripRect NOTIFY layoutChanged)
    Q_PROPERTY(int slideDistance READ slideDistance NOTIFY layoutChanged)
    Q_PROPERTY(bool revealed READ revealed NOTIFY revealedChanged)
    Q_PROPERTY(bool shrunk READ shrunk NOTIFY shrunkChanged)
    Q_PROPERTY(bool keyboardActive READ keyboardActive NOTIFY keyboardActiveChanged)
    Q_PROPERTY(QVariantList menuRows READ menuRows NOTIFY menuRowsChanged)
public:
    struct Options {
        QString configPath;
        QString engine = QStringLiteral("auto");      // auto (taos-window when installed) | cli | sway (built-in fallback)
        QStringList appDirs;                          // empty: the XDG directories
        bool watch = true;
        QString swaySocket;
        QString cliProgram = QStringLiteral("taos-window");   // the real window engine's command
        QString modePath;                             // desktop.json (default: DesktopModeFile::path())
    };
    explicit DockController(const Options &o, QObject *parent = nullptr);
    void start();

    DockModel *model() { return &m_model; }
    TaskbarModel *taskbar() { return &m_taskbar; }
    DockSettings *settings() { return &m_settings; }
    DesktopIndex *index() { return &m_index; }
    WindowEngine *engine() { return m_engine; }
    QString position() const { return m_settings.position(); }
    bool vertical() const { return m_layout.vertical; }
    bool attached() const { return m_settings.attached(); }
    // dock.json autohide, or a tiling workspace with hideInTiling: no reserved space, slides away.
    bool autohide() const { return effectiveAutohide(); }
    bool effectiveAutohide() const;
    QString currentWorkspace() const;
    bool tilingNow() const;
    DesktopModeWatcher *modes() { return &m_modes; }
    int iconSize() const { return m_layout.iconSize; }
    int cell() const { return m_layout.cell; }
    int separatorWidth() const { return m_layout.separatorWidth; }
    int padding() const { return m_layout.padding; }
    int radius() const { return m_layout.radius; }
    QRect stripRect() const { return m_layout.strip; }
    int slideDistance() const { return m_layout.slideDistance(); }
    bool magnify() const { return m_settings.magnify(); }
    bool revealed() const { return m_revealed; }
    // Auto-hidden for a moment already: the surface is only the thin edge line, so it blurs and covers nothing.
    bool shrunk() const { return m_shrunk; }
    bool keyboardActive() const { return m_kbd; }
    QVariantList menuRows() const { return m_menuRows; }

    const Layout &layout() const { return m_layout; }
    void setOutputSize(const QSize &logical);
    QSize outputSize() const { return m_output; }
    // The pinned list for a person without a saved one: only entries that exist.
    static QStringList defaultPinned(const DesktopIndex &idx);

    // Taskbar (layout.dock "taskbar"): the start button opens the launcher; a window button focuses its window, or
    // minimises it when it is the active one, or restores it when minimised.
    Q_INVOKABLE void taskbarStart();
    Q_INVOKABLE void taskbarActivate(int windowId);
    // The share-out of the width among the buttons: {buttonWidth, first, visible, hidden} (TaskbarLayout::compute).
    Q_INVOKABLE QVariantMap taskbarLayout(int available, int minW, int maxW, int gap, int moreW) const;
    Q_INVOKABLE void taskbarMenu(int windowId, qreal x, qreal y);      // right click: restore / minimise / close
    Q_INVOKABLE void taskbarOverflowMenu(int first, int visible, qreal x, qreal y);   // the windows that did not fit
    Q_INVOKABLE void activate(int row);
    Q_INVOKABLE void middleClick(int row);
    // Builds the right-click menu of `row` (-1: the strip) and tells the surface owner to show it at (x, y) in surface coordinates.
    Q_INVOKABLE void openMenu(int row, qreal x, qreal y);
    Q_INVOKABLE void openWindowList(int row, qreal x, qreal y);
    Q_INVOKABLE void invokeMenu(int index);
    Q_INVOKABLE void invokeSub(int index, int sub);   // an inline button of a window row
    Q_INVOKABLE void closeMenu();
    Q_INVOKABLE void openSettings();        // the Dock settings popover
    Q_INVOKABLE void setPointerInside(bool inside);
    Q_INVOKABLE void reveal();
    Q_INVOKABLE void hideNow();
    Q_INVOKABLE void showTip(const QString &text, qreal x, qreal y);   // x, y: item centre in surface coordinates
    Q_INVOKABLE void hideTip();
    Q_INVOKABLE void grabKeyboard();      // Super+D: show the dock and take the keyboard until Escape
    Q_INVOKABLE void releaseKeyboard();
    // reveal | hide | toggle | focus (what taos-dock <word> sends)
    void handleCommand(const QString &word);
    Q_INVOKABLE void movePinned(int from, int to);
    Q_INVOKABLE void setSetting(const QString &key, const QString &value) { m_settings.set(key, value); }

    // Performs an action (also used by the tests).
    void perform(const DockAction &a);
    bool launch(const QString &desktopId, bool newWindow);
    void setLauncherForTests(std::function<void(const QString &, const QStringList &)> f) { m_spawn = std::move(f); }
    void refreshModel();

signals:
    void layoutChanged();
    void revealedChanged();
    void keyboardActiveChanged();
    void shrunkChanged();
    void tipRequested(const QString &text, qreal x, qreal y);
    void tipHidden();
    void menuRowsChanged();
    void menuRequested(qreal x, qreal y, int row);   // the surface owner opens the menu overlay
    void menuClosed();
    void settingsRequested();

private:
    void relayout();
    void updateReveal();
    void windowAction(const QString &id);
    Options m_o;
    DockSettings m_settings;
    DesktopModeWatcher m_modes;
    DesktopIndex m_index;
    DockModel m_model;
    TaskbarModel m_taskbar;
    WindowEngine *m_engine = nullptr;
    Layout m_layout;
    QSize m_output = QSize(1920, 1080);
    QTimer m_hideTimer, m_shrinkTimer;
    bool m_shrunk = false;
    bool m_kbd = false;
    bool m_inside = false, m_menuOpen = false, m_revealed = true;
    QVariantList m_menuRows;
    QVector<DockMenuRow> m_menu;
    int m_menuRow = -1;
    std::function<void(const QString &, const QStringList &)> m_spawn;
};
