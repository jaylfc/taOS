#pragma once
#include <QElapsedTimer>
#include <QObject>
#include <QRectF>
#include <QSize>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>

#include "appinfo.h"
#include "capture.h"
#include "swayipc.h"
#include "thumbnails.h"
#include "windowlist.h"
#include "windowmodel.h"

// The brain of the overlay: tracks sway, owns the window model, handles commands from the control
// socket and exposes everything QML needs as the singleton `Overview`.
class Controller : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString mode READ mode NOTIFY modeChanged)               // hidden | switcher | overview
    Q_PROPERTY(bool windowVisible READ windowVisible NOTIFY windowVisibleChanged)
    Q_PROPERTY(QObject *windows READ windowsObject CONSTANT)
    Q_PROPERTY(int selectedIndex READ selectedIndex WRITE setSelectedIndex NOTIFY selectedIndexChanged)
    Q_PROPERTY(QString filterText READ filterText NOTIFY filterTextChanged)
    Q_PROPERTY(QVariantMap layout READ layout NOTIFY layoutChanged)     // conId (string) -> rect, overview
    Q_PROPERTY(QVariantList workspaces READ workspaces NOTIFY workspacesChanged)
    Q_PROPERTY(int windowCount READ windowCount NOTIFY countChanged)
    Q_PROPERTY(QVariantList aspects READ aspects NOTIFY countChanged)   // width/height of every window, model order
    Q_PROPERTY(QString outputName READ outputName NOTIFY outputNameChanged)
    Q_PROPERTY(bool sticky READ sticky NOTIFY modeChanged)
    Q_PROPERTY(bool framed READ framed NOTIFY framedChanged)
    Q_PROPERTY(QString currentWorkspace READ currentWorkspace NOTIFY workspacesChanged)
public:
    explicit Controller(AppInfo *apps, QObject *parent = nullptr);
    void setThumbStore(ThumbStore *s) { m_store = s; }
    void setCapture(CaptureWorker *c);
    // Connects to sway; false when there is no socket.
    bool connectSway(QString *error);
    // Used by tests: feed a parsed snapshot instead of talking to sway.
    void applySnapshot(const Snapshot &s);
    WindowModel *model() { return &m_model; }

    QString mode() const { return m_mode; }
    bool windowVisible() const { return m_windowVisible; }
    QObject *windowsObject() { return &m_model; }
    int selectedIndex() const { return m_selected; }
    void setSelectedIndex(int i);
    QString filterText() const { return m_filter; }
    QVariantMap layout() const { return m_layoutMap; }
    QVariantList workspaces() const { return m_wsList; }
    int windowCount() const { return int(m_model.rowCount()); }
    QVariantList aspects() const {
        QVariantList l;
        for (const WinInfo &w : m_model.all()) l << (w.rect.height() > 0 ? double(w.rect.width()) / w.rect.height() : 1.6);
        return l;
    }
    QString outputName() const { return m_output; }
    bool sticky() const { return m_sticky; }
    QString currentWorkspace() const { return m_curWs; }
    bool framed() const { return m_framed; }
    // The host reports the first frame of a show; the elapsed time since the command is kept for `status`.
    void noteFirstFrame();
    void noteHidden() { m_framed = false; emit framedChanged(); }
    QObject *accessibleTarget = nullptr;

    // The control socket's verbs. Returns the reply line.
    QString handleCommand(const QStringList &words);

    Q_INVOKABLE void setOverviewArea(const QRectF &area);
    Q_INVOKABLE void setCardSize(const QSize &s);
    Q_INVOKABLE void setMiniSize(const QSize &s);
    Q_INVOKABLE void setDevicePixelRatio(qreal r) { m_dpr = r; }
    Q_INVOKABLE void focusWindow(int conId);
    Q_INVOKABLE void closeWindow(int conId);
    Q_INVOKABLE void goToWorkspace(const QString &name, int num);
    Q_INVOKABLE void commit();                 // focus the selection, hide
    Q_INVOKABLE void cancel();                 // hide, focus unchanged
    Q_INVOKABLE void closeFinished();          // QML: the fade-out is done
    Q_INVOKABLE void windowActivated(bool altHeld);   // host: overlay got keyboard focus
    Q_INVOKABLE void move(const QString &dir);        // left right up down next prev
    Q_INVOKABLE void typeText(const QString &t);
    Q_INVOKABLE void backspace();
    Q_INVOKABLE void clearFilter();
    Q_INVOKABLE void announce(const QString &text);
    Q_INVOKABLE void closeSelected();
    Q_INVOKABLE void touch();                  // any input: restarts the idle fail-safe
    Q_INVOKABLE void setAccessibleTarget(QObject *o) { accessibleTarget = o; }
    Q_INVOKABLE int rowCountForFilter() const { return m_layoutIds.size(); }

signals:
    void modeChanged();
    void windowVisibleChanged();
    void selectedIndexChanged();
    void filterTextChanged();
    void layoutChanged();
    void workspacesChanged();
    void countChanged();
    void framedChanged();
    void outputNameChanged();
    void showWindow(const QString &output);     // host: create/show the surface on this output
    void hideWindow();                          // host: destroy the surface
    void releaseKeyboard();                     // host: stop owning the keyboard (closing)
    void shown();                               // first frame requested; used for timing

private:
    void refresh();
    void scheduleRefresh();
    void onEvent(quint32 type, const QByteArray &payload);
    void rebuildModel();
    void open(const QString &mode, bool reverse, bool sticky);
    void startClose();
    void recomputeLayout();
    void rebuildWorkspaces();
    void updateCapture();
    void setMode(const QString &m);
    bool matches(const WinInfo &w) const;
    QString iconFor(const QString &appId);
    int overviewNeighbour(const QString &dir) const;
    void selectionAnnounce();
    QString statusJson();

    SwayIpc m_cmd, m_ev;
    MruTracker m_mru;
    Snapshot m_snap;
    WindowModel m_model;
    ThumbStore *m_store = nullptr;
    CaptureWorker *m_cap = nullptr;
    AppInfo *m_apps = nullptr;
    QString m_mode = QStringLiteral("hidden");
    bool m_windowVisible = false;
    bool m_sticky = false;
    int m_selected = -1;
    QString m_filter;
    QString m_output, m_curWs;
    QVariantMap m_layoutMap;
    QVariantList m_wsList;
    QVector<int> m_layoutIds;           // conIds in the overview layout, reading order
    QRectF m_area;
    QSize m_card = QSize(260, 160), m_mini = QSize(220, 124);
    qreal m_dpr = 1.0;
    QTimer m_refreshTimer;
    QTimer m_idle;                      // fail-safe: closes the overlay after a quiet spell
    QHash<QString, QString> m_iconCache;
    bool m_haveSnapshot = false;
    qint64 m_lastStatusMs = 0;
    bool m_framed = false;
    QElapsedTimer m_showTimer;
    qint64 m_lastShowMs = -1;
};
