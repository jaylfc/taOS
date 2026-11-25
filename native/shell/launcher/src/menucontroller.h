// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>
#include <QObject>
#include <QVariantMap>
#include <functional>
#include <memory>

#include "actionmenu.h"
#include "appmenu.h"
#include "menunav.h"

namespace taos::launcher {

// Apps for the faces: one row per app, with the section title on the first row of a group (category or letter).
class AppListModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { IdRole = Qt::UserRole + 1, NameRole, IconRole, HasIconRole, GroupRole, CategoryRole, SectionRole, FirstInSectionRole, PinnedRole, KindRole, SubtitleRole, AccessibleRole };
    using QAbstractListModel::QAbstractListModel;
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : m_apps.size(); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setApps(const QList<taos::search::MenuApp> &apps, const QStringList &sections = {});   // sections: one title per app, or empty
    const taos::search::MenuApp *at(int i) const { return i >= 0 && i < m_apps.size() ? &m_apps[i] : nullptr; }
    int count() const { return m_apps.size(); }
private:
    QList<taos::search::MenuApp> m_apps;
    QStringList m_sections;
};

class ActionListModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { IdRole = Qt::UserRole + 1, TitleRole, SubtitleRole, IconRole, HasIconRole, IsGroupRole, ConfirmRole, AccessibleRole };
    using QAbstractListModel::QAbstractListModel;
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : m_nodes.size(); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setNodes(const QList<taos::search::ActionNode> &n);
    const taos::search::ActionNode *at(int i) const { return i >= 0 && i < m_nodes.size() ? &m_nodes[i] : nullptr; }
    int count() const { return m_nodes.size(); }
private:
    QList<taos::search::ActionNode> m_nodes;
};

// The state behind every face of the app menu. The launcher's Controller owns one and hands it the "perform this
// result" hook, so launching, the confirm step for power actions, frecency and closing behave exactly as for search.
class MenuController : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject *pinned READ pinnedModel CONSTANT)
    Q_PROPERTY(QObject *recent READ recentModel CONSTANT)
    Q_PROPERTY(QObject *all READ allModel CONSTANT)
    Q_PROPERTY(QObject *actions READ actionModel CONSTANT)
    Q_PROPERTY(QObject *power READ powerModel CONSTANT)
    Q_PROPERTY(QString sort READ sort NOTIFY sortChanged)                  // category | az
    Q_PROPERTY(bool actionsMode READ actionsMode NOTIFY actionsModeChanged)
    Q_PROPERTY(QStringList actionPath READ actionPath NOTIFY actionsModeChanged)
    Q_PROPERTY(QString actionTitle READ actionTitle NOTIFY actionsModeChanged)
    Q_PROPERTY(QString zone READ zone NOTIFY cursorChanged)
    Q_PROPERTY(int index READ index NOTIFY cursorChanged)
    Q_PROPERTY(int actionIndex READ actionIndex NOTIFY cursorChanged)
    Q_PROPERTY(int powerIndex READ powerIndex NOTIFY cursorChanged)
    Q_PROPERTY(bool contextOpen READ contextOpen NOTIFY contextChanged)
    Q_PROPERTY(QStringList contextLabels READ contextLabels NOTIFY contextChanged)
    Q_PROPERTY(QStringList contextDanger READ contextDanger NOTIFY contextChanged)
    Q_PROPERTY(int contextIndex READ contextIndex NOTIFY contextChanged)
    Q_PROPERTY(QString contextTitle READ contextTitle NOTIFY contextChanged)
    Q_PROPERTY(bool infoOpen READ infoOpen NOTIFY contextChanged)
    Q_PROPERTY(QVariantMap info READ info NOTIFY contextChanged)
    Q_PROPERTY(bool powerOpen READ powerOpen NOTIFY contextChanged)
    Q_PROPERTY(QString confirmId READ confirmId NOTIFY contextChanged)
    Q_PROPERTY(QString userName READ userName CONSTANT)
    Q_PROPERTY(QString userAvatar READ userAvatar CONSTANT)
    Q_PROPERTY(QStringList footerIds READ footerIds NOTIFY footerChanged)
    Q_PROPERTY(QStringList footerLabels READ footerLabels NOTIFY footerChanged)
    Q_PROPERTY(int pinnedCount READ pinnedCount NOTIFY modelsChanged)
    Q_PROPERTY(int allCount READ allCount NOTIFY modelsChanged)
public:
    using Perform = std::function<bool(const taos::search::Result &, int)>;
    MenuController(std::shared_ptr<taos::search::AppMenu> apps, std::shared_ptr<taos::search::ActionMenu> actions, QObject *parent = nullptr);
    void setPerform(Perform p) { m_perform = std::move(p); }
    void setNotify(std::function<void(const QString &)> n) { m_notify = std::move(n); }
    taos::search::AppMenu *appMenu() const { return m_apps.get(); }

    QObject *pinnedModel() { return &m_pinned; }
    QObject *recentModel() { return &m_recent; }
    QObject *allModel() { return &m_all; }
    QObject *actionModel() { return &m_actionModel; }
    QObject *powerModel() { return &m_powerModel; }
    AppListModel &pinnedList() { return m_pinned; }
    AppListModel &recentList() { return m_recent; }
    AppListModel &allList() { return m_all; }
    ActionListModel &actionList() { return m_actionModel; }
    QString sort() const { return m_sort; }
    bool actionsMode() const { return m_actionsMode; }
    QStringList actionPath() const { return m_path; }
    QString actionTitle() const;
    QString zone() const { return m_nav.zone(); }
    int index() const { return m_nav.index(); }
    int actionIndex() const { return m_actionIdx; }
    int powerIndex() const { return m_powerIdx; }
    bool contextOpen() const { return !m_ctx.isEmpty(); }
    QStringList contextLabels() const;
    QStringList contextDanger() const;
    int contextIndex() const { return m_ctxIdx; }
    QString contextTitle() const { return m_ctxApp.name; }
    bool infoOpen() const { return m_infoOpen; }
    QVariantMap info() const { return m_info; }
    bool powerOpen() const { return m_powerOpen; }
    QString confirmId() const { return m_confirmId; }
    QString userName() const { return m_user; }
    QString userAvatar() const { return m_avatar; }
    QStringList footerIds() const;
    QStringList footerLabels() const;
    int pinnedCount() const { return m_pinned.count(); }
    int allCount() const { return m_all.count(); }
    MenuNav &nav() { return m_nav; }
    // the app under the cursor, or an empty one
    taos::search::MenuApp currentApp() const;

    // the layout's shape: "centered" | "anchored" | "ring" | "text" | "phone"
    Q_INVOKABLE void setLayout(const QString &style);
    Q_INVOKABLE void setColumns(const QString &zone, int columns);
    Q_INVOKABLE void setSort(const QString &s);
    Q_INVOKABLE void toggleSort();
    Q_INVOKABLE void reset();                       // the launcher opens: no cursor, no layers, fresh lists
    Q_INVOKABLE bool move(int dx, int dy);
    Q_INVOKABLE bool enter();                       // Enter: true when something took it
    Q_INVOKABLE bool back();                        // Escape / Backspace: closes the topmost layer; false when none was open
    Q_INVOKABLE void hover(const QString &zone, int index);
    Q_INVOKABLE void activate(const QString &zone, int index);      // a click
    Q_INVOKABLE void openContext();                 // the app under the cursor
    Q_INVOKABLE void openContextFor(const QString &zone, int index);   // right click, long press
    Q_INVOKABLE void closeContext();
    Q_INVOKABLE void setContextIndex(int i);
    Q_INVOKABLE void runContext(int i);
    Q_INVOKABLE void pinToggle(const QString &appId);
    Q_INVOKABLE void movePinned(const QString &appId, int toIndex);
    Q_INVOKABLE void openPower();
    Q_INVOKABLE void closePower();
    Q_INVOKABLE void runPower(int i);
    Q_INVOKABLE void runFooter(int i);
    Q_INVOKABLE void enterActions(bool on);
    Q_INVOKABLE void setActionFilter(const QString &text);
    Q_INVOKABLE void setActionIndex(int i);
    Q_INVOKABLE bool actionsUp();                   // one level up; false at the top
    Q_INVOKABLE QVariantMap infoFor(const QString &appId) const;

    // verbs the Controller does not run itself ("menu-pin", "menu-unpin", "menu-info", "menu-move")
    bool handleVerb(const taos::search::Action &a);
    // IPC / CLI face: returns the reply text after "ok " (JSON) or an "error:" line.
    QString handleCommand(const QStringList &words);

signals:
    void sortChanged();
    void actionsModeChanged();
    void cursorChanged();
    void contextChanged();
    void footerChanged();
    void modelsChanged();

private:
    void reload();
    void syncNav();
    void refreshActions();
    bool performApp(const taos::search::MenuApp &a);
    bool performNode(const taos::search::ActionNode &n);
    taos::search::Result resultFor(const taos::search::MenuApp &a) const;
    taos::search::Result resultFor(const taos::search::ActionNode &n) const;
    void say(const QString &s) { if (m_notify) m_notify(s); }
    std::shared_ptr<taos::search::AppMenu> m_apps;
    std::shared_ptr<taos::search::ActionMenu> m_actionsEngine;
    AppListModel m_pinned, m_recent, m_all;
    ActionListModel m_actionModel, m_powerModel;
    MenuNav m_nav;
    Perform m_perform;
    std::function<void(const QString &)> m_notify;
    QString m_sort = QStringLiteral("category"), m_style = QStringLiteral("centered"), m_filter;
    bool m_actionsMode = false, m_infoOpen = false, m_powerOpen = false;
    QStringList m_path;
    int m_actionIdx = 0, m_powerIdx = 0, m_ctxIdx = 0;
    QList<taos::search::Action> m_ctx;
    taos::search::MenuApp m_ctxApp;
    QVariantMap m_info;
    QString m_confirmId, m_user, m_avatar;
    struct Footer { QString id, label; };
    QList<Footer> m_footer;
};

}  // namespace taos::launcher
