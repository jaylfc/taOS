// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>
#include <QColor>
#include <QString>
#include <QVector>

struct UserEntry {
    QString username;
    QString displayName;
    QString iconPath;  // absolute path of a readable image, or empty
    uint uid = 0;
};

// Pure helpers (unit tested without any system access).
namespace UserDiscovery {
// /etc/passwd text -> login users: uid 1000..59999 with a real login shell.
QVector<UserEntry> parsePasswd(const QByteArray &passwd);
// First comma separated GECOS field, trimmed; the username when it is empty.
QString displayNameFromGecos(const QString &gecos, const QString &username);
// "Alex Morgan" -> "AM", "alex" -> "A", "" -> "?". Non-ASCII safe.
QString monogram(const QString &displayName, const QString &username);
// Deterministic gradient stops for a username, in the accent colour family.
QPair<QColor, QColor> avatarColors(const QString &username, const QColor &accent);
// First readable image among the AccountsService icon, the AccountsService
// icons directory and ~/.face; empty when none.
QString findIcon(const QString &username, const QString &accountsIconFile, const QString &home,
                 const QString &iconsDir = QStringLiteral("/var/lib/AccountsService/icons"));
}  // namespace UserDiscovery

// UserModel: the people the user selector shows.
class UserModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
public:
    enum Roles { UsernameRole = Qt::UserRole + 1, DisplayNameRole, IconRole, InitialsRole, ColorARole, ColorBRole };
    explicit UserModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}

    void setAccent(const QColor &c) {
        if (c == m_accent) return;
        m_accent = c;
        if (!m_users.isEmpty()) emit dataChanged(index(0), index(int(m_users.size()) - 1), {ColorARole, ColorBRole});
    }
    // Discover: AccountsService over D-Bus when it answers with users (and
    // useDbus), else the passwd file. Never throws; an empty model is valid.
    void discover(bool useDbus, const QString &passwdPath = QStringLiteral("/etc/passwd"));
    void setUsers(const QVector<UserEntry> &users);

    int rowCount(const QModelIndex & = QModelIndex()) const override { return int(m_users.size()); }
    QVariant data(const QModelIndex &idx, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    Q_INVOKABLE QString usernameAt(int i) const;
    Q_INVOKABLE QString displayNameAt(int i) const;
    uint uidAt(int i) const { return i >= 0 && i < m_users.size() ? m_users.at(i).uid : 0; }
    Q_INVOKABLE int indexOfUser(const QString &username) const;
signals:
    void countChanged();

private:
    static QVector<UserEntry> fromAccountsService();
    QVector<UserEntry> m_users;
    QColor m_accent;  // the theme accent: its hue anchors the per person avatar colours
};
