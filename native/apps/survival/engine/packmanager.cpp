// SPDX-License-Identifier: AGPL-3.0-or-later
#include "packmanager.h"

#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QStorageInfo>
#include <QtConcurrent>

namespace Taos::Survival {

namespace {
QString normSha(const QString &s) { return s.trimmed().toLower(); }
bool safeFileName(const QString &f)
{
    return !f.isEmpty() && !f.contains(u'/') && !f.contains(u'\\') && f != QStringLiteral(".") && f != QStringLiteral("..")
        && !f.startsWith(u'.');
}
QString kindOfFile(const QString &name)
{
    if (name.endsWith(QStringLiteral(".zim"), Qt::CaseInsensitive)) return QStringLiteral("zim");
    if (name.endsWith(QStringLiteral(".pmtiles"), Qt::CaseInsensitive)) return QStringLiteral("map");
    return {};
}
} // namespace

PackManager::PackManager(const QString &dataDir, QObject *parent) : QObject(parent), m_dir(dataDir)
{
    QDir().mkpath(packsDir());
    rescan();
}

bool PackManager::loadCatalogue(const QByteArray &json, QString *error)
{
    QJsonParseError pe;
    const QJsonDocument doc = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) {
        if (error) *error = QStringLiteral("The pack catalogue is not valid JSON.");
        return false;
    }
    m_catalogue.clear();
    m_order.clear();
    for (const QJsonValue &v : doc.object().value(QStringLiteral("packs")).toArray()) {
        const QJsonObject o = v.toObject();
        PackInfo p;
        p.id = o.value(QStringLiteral("id")).toString();
        p.title = o.value(QStringLiteral("title")).toString();
        p.description = o.value(QStringLiteral("description")).toString();
        p.kind = o.value(QStringLiteral("kind")).toString();
        p.language = o.value(QStringLiteral("language")).toString();
        p.region = o.value(QStringLiteral("region")).toString();
        p.file = o.value(QStringLiteral("file")).toString();
        p.url = QUrl(o.value(QStringLiteral("url")).toString());
        p.recipe = o.value(QStringLiteral("recipe")).toString();
        p.bytes = qint64(o.value(QStringLiteral("bytes")).toDouble());
        p.sha256 = normSha(o.value(QStringLiteral("sha256")).toString());
        p.licence = o.value(QStringLiteral("licence")).toString();
        p.licenceUrl = o.value(QStringLiteral("licenceUrl")).toString();
        p.attribution = o.value(QStringLiteral("attribution")).toString();
        p.publisher = o.value(QStringLiteral("publisher")).toString();
        p.version = o.value(QStringLiteral("version")).toString();
        p.needsAcceptance = o.value(QStringLiteral("needsAcceptance")).toBool();
        if (p.id.isEmpty() || !safeFileName(p.file) || (p.kind != QStringLiteral("zim") && p.kind != QStringLiteral("map")))
            continue;   // a malformed entry is skipped, not trusted
        m_catalogue.insert(p.id, p);
        m_order.append(p.id);
    }
    rescan();
    return true;
}

bool PackManager::loadCatalogueFile(const QString &path, QString *error)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("Cannot read the pack catalogue.");
        return false;
    }
    return loadCatalogue(f.readAll(), error);
}

void PackManager::rescan()
{
    m_local.clear();
    QSet<QString> known;
    for (const PackInfo &c : std::as_const(m_catalogue))
        known.insert(c.file);
    const QDir d(packsDir());
    for (const QFileInfo &fi : d.entryInfoList(QDir::Files | QDir::NoDotAndDotDot)) {
        const QString kind = kindOfFile(fi.fileName());
        if (kind.isEmpty() || known.contains(fi.fileName()))
            continue;
        PackInfo p;
        p.id = QStringLiteral("local-") + fi.completeBaseName();
        p.title = fi.completeBaseName();
        p.kind = kind;
        p.file = fi.fileName();
        p.local = true;
        // a record written at import time names it properly
        QFile rec(recordPath(fi.fileName()));
        if (rec.open(QIODevice::ReadOnly)) {
            const QJsonObject o = QJsonDocument::fromJson(rec.readAll()).object();
            if (!o.value(QStringLiteral("title")).toString().isEmpty()) p.title = o.value(QStringLiteral("title")).toString();
            p.sha256 = o.value(QStringLiteral("sha256")).toString();
        }
        m_local.insert(p.id, p);
    }
    Q_EMIT changed();
}

PackInfo PackManager::pack(const QString &id) const
{
    PackInfo p = m_catalogue.contains(id) ? m_catalogue.value(id) : m_local.value(id);
    if (p.id.isEmpty())
        return p;
    const QFileInfo fi(packsDir() + QLatin1Char('/') + p.file);
    p.licenceAccepted = licenceAccepted(id);
    if (fi.exists() && fi.isFile() && !m_jobs.contains(id)) {
        p.installed = true;
        p.path = fi.absoluteFilePath();
        p.installedBytes = fi.size();
        p.state = QStringLiteral("installed");
    } else {
        p.state = QStringLiteral("available");
    }
    if (m_jobs.contains(id)) {
        p.state = QStringLiteral("downloading");
        p.received = m_jobs.value(id).resumeFrom;
        if (m_jobs.value(id).file)
            p.received = m_jobs.value(id).file->size();
    }
    if (m_state.contains(id))
        p.state = m_state.value(id);
    if (m_errors.contains(id))
        p.error = m_errors.value(id);
    if (!p.installed && p.state == QStringLiteral("available")) {
        const QFileInfo part(packsDir() + QLatin1Char('/') + p.file + QStringLiteral(".part"));
        if (part.exists())
            p.received = part.size();
    }
    return p;
}

QList<PackInfo> PackManager::packs() const
{
    QList<PackInfo> out;
    for (const QString &id : m_order)
        out.append(pack(id));
    QStringList locals = m_local.keys();
    locals.sort();
    for (const QString &id : std::as_const(locals))
        out.append(pack(id));
    return out;
}

bool PackManager::licenceAccepted(const QString &id) const
{
    QFile f(m_dir + QStringLiteral("/licences.json"));
    if (!f.open(QIODevice::ReadOnly))
        return false;
    const PackInfo p = m_catalogue.value(id);
    const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object().value(id).toObject();
    return !o.isEmpty() && o.value(QStringLiteral("licence")).toString() == p.licence
        && o.value(QStringLiteral("version")).toString() == p.version;
}

void PackManager::acceptLicence(const QString &id)
{
    if (!m_catalogue.contains(id))
        return;
    const PackInfo p = m_catalogue.value(id);
    const QString path = m_dir + QStringLiteral("/licences.json");
    QJsonObject all;
    QFile f(path);
    if (f.open(QIODevice::ReadOnly))
        all = QJsonDocument::fromJson(f.readAll()).object();
    f.close();
    all.insert(id, QJsonObject{{QStringLiteral("licence"), p.licence}, {QStringLiteral("version"), p.version},
                               {QStringLiteral("at"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}});
    QFile out(path);
    if (out.open(QIODevice::WriteOnly | QIODevice::Truncate))
        out.write(QJsonDocument(all).toJson());
    Q_EMIT changed();
}

QString PackManager::sha256Of(const QString &file)
{
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return {};
    QCryptographicHash h(QCryptographicHash::Sha256);
    if (!h.addData(&f))
        return {};
    return QString::fromLatin1(h.result().toHex());
}

VerifyResult PackManager::verifyFile(const QString &file, const QString &expected)
{
    if (normSha(expected).isEmpty())
        return VerifyResult::NoChecksum;
    const QString got = sha256Of(file);
    if (got.isEmpty())
        return VerifyResult::Unreadable;
    return got == normSha(expected) ? VerifyResult::Ok : VerifyResult::Mismatch;
}

VerifyResult PackManager::verifyInstalled(const QString &id) const
{
    const PackInfo p = pack(id);
    if (!p.installed)
        return VerifyResult::Unreadable;
    QString expected = p.sha256;
    if (expected.isEmpty()) {
        QFile rec(recordPath(p.file));
        if (rec.open(QIODevice::ReadOnly))
            expected = QJsonDocument::fromJson(rec.readAll()).object().value(QStringLiteral("sha256")).toString();
    }
    return verifyFile(p.path, expected);
}

void PackManager::writeRecord(const PackInfo &p, const QString &sha) const
{
    QJsonObject o{{QStringLiteral("id"), p.id}, {QStringLiteral("title"), p.title}, {QStringLiteral("sha256"), sha},
                  {QStringLiteral("licence"), p.licence}, {QStringLiteral("version"), p.version},
                  {QStringLiteral("verifiedAt"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}};
    QFile f(recordPath(p.file));
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(o).toJson());
}

bool PackManager::download(const QString &id, QString *error)
{
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    if (!m_catalogue.contains(id))
        return fail(QStringLiteral("This pack is not in the catalogue."));
    const PackInfo p = m_catalogue.value(id);
    if (m_jobs.contains(id))
        return fail(QStringLiteral("This pack is already downloading."));
    // file: addresses exist for the tests only; a catalogue from outside can never make the app read a local file
    const bool fileOk = p.url.scheme() == QStringLiteral("file") && qEnvironmentVariableIsSet("TAOS_SURVIVAL_ALLOW_FILE_URLS");
    if (p.url.isEmpty() || !(p.url.scheme() == QStringLiteral("https") || p.url.scheme() == QStringLiteral("http") || fileOk))
        return fail(p.recipe.isEmpty() ? QStringLiteral("This pack has no download address.")
                                       : QStringLiteral("This pack has no publisher download. See how to make it."));
    if (p.sha256.isEmpty())
        return fail(QStringLiteral("This pack has no checksum, so it cannot be verified. Download refused."));
    if (p.needsAcceptance && !licenceAccepted(id))
        return fail(QStringLiteral("Read and accept the licence first."));
    const QString part = packsDir() + QLatin1Char('/') + p.file + QStringLiteral(".part");
    const qint64 have = QFileInfo(part).exists() ? QFileInfo(part).size() : 0;
    const QStorageInfo st(packsDir());
    if (p.bytes > 0 && st.isValid() && st.bytesAvailable() < (p.bytes - have) + (qint64(1) << 30))
        return fail(QStringLiteral("Not enough free space for this pack."));
    m_errors.remove(id);
    m_state.remove(id);
    Job j;
    j.partPath = part;
    m_jobs.insert(id, j);
    startRequest(id);
    Q_EMIT changed();
    return true;
}

void PackManager::startRequest(const QString &id)
{
    Job &j = m_jobs[id];
    const PackInfo p = m_catalogue.value(id);
    auto *file = new QFile(j.partPath, this);
    const qint64 have = QFileInfo(j.partPath).exists() ? QFileInfo(j.partPath).size() : 0;
    if (!file->open(QIODevice::WriteOnly | QIODevice::Append)) {
        delete file;
        m_jobs.remove(id);
        m_errors.insert(id, QStringLiteral("Cannot write to the packs folder."));
        m_state.insert(id, QStringLiteral("failed"));
        Q_EMIT finished(id, false, m_errors.value(id));
        Q_EMIT changed();
        return;
    }
    j.file = file;
    j.resumeFrom = have;
    QNetworkRequest req(p.url);
    req.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
    req.setTransferTimeout(60 * 1000);
    if (have > 0)
        req.setRawHeader("Range", "bytes=" + QByteArray::number(have) + "-");
    QNetworkReply *r = m_nam.get(req);
    j.reply = r;
    connect(r, &QNetworkReply::readyRead, this, [this, id, r] {
        if (!m_jobs.contains(id)) return;
        Job &jj = m_jobs[id];
        const qint64 status = r->attribute(QNetworkRequest::HttpStatusCodeAttribute).toLongLong();
        if (status == 200 && jj.resumeFrom > 0 && jj.file) {   // the server ignored Range: start over
            jj.file->resize(0);
            jj.resumeFrom = 0;
        }
        if (jj.file && status != 416 && (status == 0 || status < 400))
            jj.file->write(r->readAll());
    });
    connect(r, &QNetworkReply::downloadProgress, this, [this, id](qint64 got, qint64 total) {
        if (!m_jobs.contains(id)) return;
        const qint64 base = m_jobs.value(id).resumeFrom;
        m_jobs[id].total = total > 0 ? total + base : 0;
        Q_EMIT progress(id, base + got, total > 0 ? total + base : m_catalogue.value(id).bytes);
    });
    connect(r, &QNetworkReply::finished, this, [this, id] { finishDownload(id); });
}

void PackManager::failJob(const QString &id, const QString &message)
{
    if (m_jobs.contains(id)) {
        Job j = m_jobs.take(id);
        if (j.file) { j.file->close(); j.file->deleteLater(); }
        if (j.reply) j.reply->deleteLater();
    }
    m_errors.insert(id, message);
    m_state.insert(id, QStringLiteral("failed"));
    Q_EMIT finished(id, false, message);
    Q_EMIT changed();
}

void PackManager::finishDownload(const QString &id)
{
    if (!m_jobs.contains(id))
        return;
    Job &j = m_jobs[id];
    QNetworkReply *r = j.reply;
    if (!r) { failJob(id, QStringLiteral("The download was interrupted.")); return; }
    const qint64 status = r->attribute(QNetworkRequest::HttpStatusCodeAttribute).toLongLong();
    if (j.file && r->bytesAvailable() > 0 && (status == 0 || status < 400))
        j.file->write(r->readAll());
    if (j.file) j.file->close();
    const QNetworkReply::NetworkError err = r->error();
    const QString errText = r->errorString();
    const bool cancelled = err == QNetworkReply::OperationCanceledError;
    if (status == 416) {   // asked for bytes past the end: the partial file was complete or wrong; start clean next time
        QFile::remove(j.partPath);
        failJob(id, QStringLiteral("The partial download did not match the server. Try again."));
        return;
    }
    if (cancelled) { Job jj = m_jobs.take(id); if (jj.file) jj.file->deleteLater(); jj.reply->deleteLater(); Q_EMIT changed(); return; }
    if (err != QNetworkReply::NoError) {
        failJob(id, QStringLiteral("The download stopped: %1. It resumes where it left off.").arg(errText));
        return;
    }
    const PackInfo p = m_catalogue.value(id);
    const QString part = j.partPath;
    m_state.insert(id, QStringLiteral("verifying"));
    Q_EMIT changed();
    auto *w = new QFutureWatcher<VerifyResult>(this);
    connect(w, &QFutureWatcher<VerifyResult>::finished, this, [this, id, w, part, p] {
        const VerifyResult v = w->result();
        w->deleteLater();
        if (m_jobs.contains(id)) {
            Job jj = m_jobs.take(id);
            if (jj.file) jj.file->deleteLater();
            if (jj.reply) jj.reply->deleteLater();
        }
        m_state.remove(id);
        if (v != VerifyResult::Ok) {
            QFile::remove(part);   // a file that fails its checksum is never kept
            m_errors.insert(id, QStringLiteral("The downloaded file did not match its checksum and was deleted."));
            m_state.insert(id, QStringLiteral("failed"));
            Q_EMIT finished(id, false, m_errors.value(id));
            Q_EMIT changed();
            return;
        }
        const QString dest = packsDir() + QLatin1Char('/') + p.file;
        QFile::remove(dest);
        if (!QFile::rename(part, dest)) {
            m_errors.insert(id, QStringLiteral("Cannot move the finished download into place."));
            m_state.insert(id, QStringLiteral("failed"));
            Q_EMIT finished(id, false, m_errors.value(id));
            Q_EMIT changed();
            return;
        }
        writeRecord(p, p.sha256);
        m_errors.remove(id);
        Q_EMIT finished(id, true, QStringLiteral("Installed."));
        Q_EMIT changed();
    });
    const QString expected = p.sha256;
    w->setFuture(QtConcurrent::run([part, expected] { return PackManager::verifyFile(part, expected); }));
}

void PackManager::cancel(const QString &id)
{
    if (!m_jobs.contains(id))
        return;
    QNetworkReply *r = m_jobs.value(id).reply;
    if (r)
        r->abort();   // the partial file stays, so Download resumes
}

bool PackManager::remove(const QString &id, QString *error)
{
    const PackInfo p = pack(id);
    if (p.id.isEmpty()) {
        if (error) *error = QStringLiteral("No such pack.");
        return false;
    }
    cancel(id);
    bool ok = true;
    QStringList stuck;
    for (const QString &f : {p.file, p.file + QStringLiteral(".part"), p.file + QStringLiteral(".taospack.json")}) {
        const QString full = packsDir() + QLatin1Char('/') + f;
        if (QFileInfo::exists(full) && !QFile::remove(full)) {
            ok = false;
            stuck << f;
        }
    }
    m_errors.remove(id);
    m_state.remove(id);
    if (!ok && error)
        *error = QStringLiteral("Could not delete: %1").arg(stuck.join(QStringLiteral(", ")));
    rescan();
    return ok;
}

bool PackManager::importFile(const QString &source, const QString &expectedSha256, QString *error)
{
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    const QFileInfo si(source);
    if (!si.exists() || !si.isFile())
        return fail(QStringLiteral("That file does not exist."));
    if (kindOfFile(si.fileName()).isEmpty())
        return fail(QStringLiteral("Only .zim and .pmtiles files can be imported."));
    if (!safeFileName(si.fileName()))
        return fail(QStringLiteral("That file name cannot be used."));
    if (!normSha(expectedSha256).isEmpty() && verifyFile(source, expectedSha256) != VerifyResult::Ok)
        return fail(QStringLiteral("The file does not match the checksum you gave. Nothing was imported."));
    const QStorageInfo st(packsDir());
    if (st.isValid() && st.bytesAvailable() < si.size() + (qint64(1) << 30))
        return fail(QStringLiteral("Not enough free space to import this file."));
    const QString dest = packsDir() + QLatin1Char('/') + si.fileName();
    if (QFileInfo::exists(dest))
        return fail(QStringLiteral("A pack with this file name is already installed."));
    if (!QFile::copy(source, dest)) {
        QFile::remove(dest);
        return fail(QStringLiteral("Could not copy the file into the packs folder."));
    }
    PackInfo p;
    p.id = QStringLiteral("local-") + si.completeBaseName();
    p.title = si.completeBaseName();
    p.file = si.fileName();
    writeRecord(p, normSha(expectedSha256).isEmpty() ? sha256Of(dest) : normSha(expectedSha256));
    rescan();
    return true;
}

QJsonObject PackManager::toJson(const PackInfo &p) const
{
    return QJsonObject{{QStringLiteral("id"), p.id}, {QStringLiteral("title"), p.title}, {QStringLiteral("kind"), p.kind},
                       {QStringLiteral("state"), p.state}, {QStringLiteral("installed"), p.installed},
                       {QStringLiteral("bytes"), double(p.bytes)}, {QStringLiteral("installedBytes"), double(p.installedBytes)},
                       {QStringLiteral("licence"), p.licence}, {QStringLiteral("path"), p.path}};
}

} // namespace Taos::Survival
