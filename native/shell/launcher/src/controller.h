// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QStringList>
#include <QTimer>
#include <memory>

#include "agents.h"
#include "config.h"
#include "engine.h"
#include "frecency.h"
#include "rowmodel.h"

namespace taos::launcher {

// The launcher's brain between the engine and the QML face: the query, the rows, the selection, the
// secondary actions panel, the confirm step and what happens on Enter.
class Controller : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString query READ query NOTIFY queryChanged)
    Q_PROPERTY(bool open READ isOpen NOTIFY openChanged)
    Q_PROPERTY(QObject *rows READ rows CONSTANT)
    Q_PROPERTY(int count READ count NOTIFY rowsChanged)
    Q_PROPERTY(int currentIndex READ currentIndex NOTIFY currentChanged)
    Q_PROPERTY(bool actionsOpen READ actionsOpen NOTIFY actionsChanged)
    Q_PROPERTY(QStringList actionLabels READ actionLabels NOTIFY actionsChanged)
    Q_PROPERTY(int actionIndex READ actionIndex NOTIFY actionsChanged)
    Q_PROPERTY(QString currentHint READ currentHint NOTIFY currentChanged)
    Q_PROPERTY(QString notice READ notice NOTIFY noticeChanged)
    Q_PROPERTY(bool searching READ searching NOTIFY rowsChanged)
    Q_PROPERTY(bool launching READ launching NOTIFY launchingChanged)
    Q_PROPERTY(QString currentTitle READ currentTitle NOTIFY currentChanged)
    Q_PROPERTY(QString currentIcon READ currentIcon NOTIFY currentChanged)
    Q_PROPERTY(QString currentKind READ currentKind NOTIFY currentChanged)
public:
    Controller(taos::search::SearchEngine *engine, std::shared_ptr<taos::search::Frecency> frec, QObject *parent = nullptr);

    QString query() const { return m_query; }
    bool isOpen() const { return m_open; }
    QObject *rows() { return &m_model; }
    int count() const { return m_model.count(); }
    int currentIndex() const { return m_cur; }
    bool actionsOpen() const { return m_actionsOpen; }
    QStringList actionLabels() const;
    int actionIndex() const { return m_actionIdx; }
    QString currentHint() const;
    QString notice() const { return m_notice; }
    bool searching() const { return m_searching; }
    bool launching() const { return m_launching; }
    QString currentTitle() const;
    QString currentIcon() const;
    QString currentKind() const;
    RowModel &model() { return m_model; }
    Q_INVOKABLE void setAccessibleTarget(QObject *o) { m_a11y = o; }
    void setLaunchDelay(int ms) { m_launchMs = ms; }
    void setConfig(std::shared_ptr<taos::search::LauncherConfig> c) { m_cfg = std::move(c); }
    void setSender(std::shared_ptr<taos::search::ChatSender> s) { m_sender = std::move(s); }

    // IPC face. Return the reply text.
    QString handleCommand(const QStringList &words);

    Q_INVOKABLE void setQuery(const QString &text);
    Q_INVOKABLE void move(int delta);
    Q_INVOKABLE void hoverIndex(int i);
    Q_INVOKABLE void activate();                  // Enter
    Q_INVOKABLE void activateAlternate();         // Shift+Enter: the other send mode on a mention, else Enter
    Q_INVOKABLE void activateIndex(int i);        // click
    Q_INVOKABLE void quickPick(int n);            // Ctrl+1..9, n is 1 based
    Q_INVOKABLE void openActions();               // Tab or Right
    Q_INVOKABLE void closeActions();
    Q_INVOKABLE void moveAction(int delta);
    Q_INVOKABLE void runActionAt(int i);
    Q_INVOKABLE void setActionIndex(int i);
    Q_INVOKABLE void escape();                    // clears, then closes
    Q_INVOKABLE void hide();
    Q_INVOKABLE void closeFinished();             // the QML finished its fade out
    Q_INVOKABLE void windowDeactivated();         // lost keyboard focus
    Q_INVOKABLE void windowActivated();
    Q_INVOKABLE void announce(const QString &text);
    void show();
    void toggle();

signals:
    void queryChanged();
    void openChanged();
    void rowsChanged();
    void currentChanged();
    void actionsChanged();
    void noticeChanged();
    void launchingChanged();
    void showWindow();
    void hideWindow();
    void clearInput();        // the QML text field should empty itself
    void setInputText(const QString &text);   // autocomplete: put this in the field, cursor at the end

private:
    void onUpdated(int gen, const QList<taos::search::Row> &rows, bool final);
    void select(int i, bool byUser);
    bool perform(const taos::search::Result &r, int actionIdx);
    void finishLaunch();
    void setNotice(const QString &n);
    taos::search::SearchEngine *m_engine;
    std::shared_ptr<taos::search::Frecency> m_frec;
    RowModel m_model;
    QString m_query;
    bool m_open = false, m_actionsOpen = false, m_searching = false, m_launching = false, m_userMoved = false, m_visible = false;
    int m_cur = -1, m_actionIdx = 0, m_gen = 0;
    QString m_confirmId;      // result id waiting for a second Enter
    QString m_notice;
    QObject *m_a11y = nullptr;
    std::shared_ptr<taos::search::LauncherConfig> m_cfg;
    std::shared_ptr<taos::search::ChatSender> m_sender;
    QTimer m_launchTimer, m_noticeTimer;
    qint64 m_shownAt = 0;
    int m_launchMs = 170;
};

}  // namespace taos::launcher
