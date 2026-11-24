#include "usermodel.h"

#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusReply>
#include <QDBusObjectPath>
#include <QFile>
#include <QFileInfo>
#include <QImageReader>
#include <QStringList>
#include <QRegularExpression>
#include <QUrl>

namespace UserDiscovery {

QString displayNameFromGecos(const QString &gecos, const QString &username) {
    const QString first = gecos.section(QLatin1Char(','), 0, 0).trimmed();
    return first.isEmpty() ? username : first;
}

static bool realShell(const QString &shell) {
    const QString s = shell.trimmed();
    if (s.isEmpty()) return false;  // empty means /bin/sh by passwd rules, but never a person's seat here
    const QString base = s.section(QLatin1Char('/'), -1);
    return base != QLatin1String("nologin") && base != QLatin1String("false") && base != QLatin1String("sync") &&
           base != QLatin1String("halt") && base != QLatin1String("shutdown");
}

QVector<UserEntry> parsePasswd(const QByteArray &passwd) {
    QVector<UserEntry> out;
    const QStringList lines = QString::fromUtf8(passwd).split(QLatin1Char('\n'));
    for (const QString &line : lines) {
        if (line.isEmpty() || line.startsWith(QLatin1Char('#'))) continue;
        const QStringList f = line.split(QLatin1Char(':'));
        if (f.size() < 7) continue;
        const QString name = f[0];
        if (name.isEmpty() || name.contains(QLatin1Char(' '))) continue;
        bool ok = false;
        const uint uid = f[2].toUInt(&ok);
        if (!ok || uid < 1000 || uid > 59999) continue;
        if (!realShell(f[6])) continue;
        UserEntry e;
        e.username = name;
        e.uid = uid;
        e.displayName = displayNameFromGecos(f[4], name);
        e.iconPath = QString();
        out.append(e);
    }
    return out;
}

QString monogram(const QString &displayName, const QString &username) {
    auto firstCp = [](const QString &w) -> QString {
        const QList<uint> cps = w.toUcs4();
        for (uint cp : cps) {
            if (QChar::isLetterOrNumber(cp)) return QString::fromUcs4(&cp, 1).toUpper();
        }
        return QString();
    };
    QStringList words = displayName.split(QRegularExpression(QStringLiteral("[\\s._-]+")), Qt::SkipEmptyParts);
    QStringList letters;
    for (const QString &w : words) {
        const QString c = firstCp(w);
        if (!c.isEmpty()) letters << c;
    }
    if (letters.isEmpty()) {
        const QString c = firstCp(username);
        return c.isEmpty() ? QStringLiteral("?") : c;
    }
    if (letters.size() == 1) return letters.first();
    return letters.first() + letters.last();
}

QPair<QColor, QColor> avatarColors(const QString &username, const QColor &accent) {
    // FNV-1a over the UTF-8 bytes: stable across runs, platforms and Qt versions.
    quint32 h = 2166136261u;
    for (char ch : username.toUtf8()) {
        h ^= quint8(ch);
        h *= 16777619u;
    }
    const qreal baseHue = accent.hueF() < 0 ? 0.65 : accent.hueF();
    // Spread within a 100 degree arc centred on the accent hue.
    const qreal offset = (qreal(h % 1000) / 1000.0 - 0.5) * (100.0 / 360.0);
    qreal hue = baseHue + offset;
    while (hue < 0) hue += 1.0;
    while (hue >= 1) hue -= 1.0;
    qreal hue2 = hue + 28.0 / 360.0;
    if (hue2 >= 1) hue2 -= 1.0;
    return {QColor::fromHslF(hue, 0.52, 0.56), QColor::fromHslF(hue2, 0.56, 0.36)};
}

static bool readableImage(const QString &path) {
    if (path.isEmpty()) return false;
    const QFileInfo fi(path);
    if (!fi.isFile() || !fi.isReadable() || fi.size() <= 0 || fi.size() > 8 * 1024 * 1024) return false;
    QImageReader r(path);
    return r.canRead();
}

QString findIcon(const QString &username, const QString &accountsIconFile, const QString &home,
                 const QString &iconsDir) {
    if (readableImage(accountsIconFile)) return accountsIconFile;
    const QString viaDir = iconsDir + QLatin1Char('/') + username;
    if (readableImage(viaDir)) return viaDir;
    if (!home.isEmpty() && readableImage(home + QStringLiteral("/.face"))) return home + QStringLiteral("/.face");
    return QString();
}

}  // namespace UserDiscovery

QVector<UserEntry> UserModel::fromAccountsService() {
    QVector<UserEntry> out;
    QDBusConnection bus = QDBusConnection::systemBus();
    if (!bus.isConnected()) return out;
    QDBusInterface acc(QStringLiteral("org.freedesktop.Accounts"), QStringLiteral("/org/freedesktop/Accounts"),
                       QStringLiteral("org.freedesktop.Accounts"), bus);
    if (!acc.isValid()) return out;
    acc.setTimeout(1500);
    QDBusReply<QList<QDBusObjectPath>> r = acc.call(QStringLiteral("ListCachedUsers"));
    if (!r.isValid()) return out;
    for (const QDBusObjectPath &p : r.value()) {
        QDBusInterface props(QStringLiteral("org.freedesktop.Accounts"), p.path(),
                             QStringLiteral("org.freedesktop.DBus.Properties"), bus);
        props.setTimeout(1500);
        QDBusReply<QVariantMap> all = props.call(QStringLiteral("GetAll"), QStringLiteral("org.freedesktop.Accounts.User"));
        if (!all.isValid()) continue;
        const QVariantMap m = all.value();
        const QString name = m.value(QStringLiteral("UserName")).toString();
        if (name.isEmpty() || m.value(QStringLiteral("SystemAccount")).toBool() ||
            m.value(QStringLiteral("Locked")).toBool())
            continue;
        const QString shell = m.value(QStringLiteral("Shell")).toString();
        if (!shell.isEmpty() && !UserDiscovery::parsePasswd(QStringLiteral("x:x:1000:1000::/:%1\n").arg(shell).toUtf8()).size())
            continue;
        UserEntry e;
        e.username = name;
        e.uid = m.value(QStringLiteral("Uid")).toUInt();
        e.displayName = m.value(QStringLiteral("RealName")).toString().trimmed();
        if (e.displayName.isEmpty()) e.displayName = name;
        e.iconPath = UserDiscovery::findIcon(name, m.value(QStringLiteral("IconFile")).toString(),
                                             m.value(QStringLiteral("HomeDirectory")).toString());
        out.append(e);
    }
    return out;
}

void UserModel::discover(bool useDbus, const QString &passwdPath) {
    QVector<UserEntry> users;
    if (useDbus) users = fromAccountsService();
    if (users.isEmpty()) {
        QFile f(passwdPath);
        if (f.open(QIODevice::ReadOnly)) {
            users = UserDiscovery::parsePasswd(f.read(4 * 1024 * 1024));
            // Home directories come from the same file; re-read with the field.
            f.seek(0);
            const QStringList lines = QString::fromUtf8(f.read(4 * 1024 * 1024)).split(QLatin1Char('\n'));
            for (UserEntry &u : users) {
                for (const QString &l : lines) {
                    const QStringList fl = l.split(QLatin1Char(':'));
                    if (fl.size() >= 7 && fl[0] == u.username) {
                        u.iconPath = UserDiscovery::findIcon(u.username, QString(), fl[5]);
                        break;
                    }
                }
            }
        }
    }
    setUsers(users);
}

void UserModel::setUsers(const QVector<UserEntry> &users) {
    beginResetModel();
    m_users = users;
    endResetModel();
    emit countChanged();
}

QVariant UserModel::data(const QModelIndex &idx, int role) const {
    if (!idx.isValid() || idx.row() < 0 || idx.row() >= m_users.size()) return {};
    const UserEntry &u = m_users[idx.row()];
    switch (role) {
    case UsernameRole: return u.username;
    case DisplayNameRole: return u.displayName;
    case IconRole: return u.iconPath.isEmpty() ? QString() : QUrl::fromLocalFile(u.iconPath).toString();
    case InitialsRole: return UserDiscovery::monogram(u.displayName, u.username);
    case ColorARole: return UserDiscovery::avatarColors(u.username, m_accent).first;
    case ColorBRole: return UserDiscovery::avatarColors(u.username, m_accent).second;
    }
    return {};
}

QHash<int, QByteArray> UserModel::roleNames() const {
    return {{UsernameRole, "username"}, {DisplayNameRole, "displayName"}, {IconRole, "iconSource"},
            {InitialsRole, "initials"}, {ColorARole, "colorA"}, {ColorBRole, "colorB"}};
}

QString UserModel::usernameAt(int i) const { return i >= 0 && i < m_users.size() ? m_users[i].username : QString(); }
QString UserModel::displayNameAt(int i) const { return i >= 0 && i < m_users.size() ? m_users[i].displayName : QString(); }
int UserModel::indexOfUser(const QString &u) const {
    for (int i = 0; i < m_users.size(); ++i) if (m_users[i].username == u) return i;
    return -1;
}
