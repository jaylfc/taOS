#include "fmt.h"

#include <QRegularExpression>
#include <QUrl>
#include <cmath>

using namespace Qt::StringLiterals;

namespace Taos::Activity {

namespace {
QString trimmedNumber(double v, int decimals)
{
    return QString::number(v, 'f', decimals);
}
bool isNum(const QVariant &v)
{
    return v.isValid() && !v.isNull() && v.canConvert<double>() && v.typeId() != QMetaType::QString
        && v.typeId() != QMetaType::Bool;
}
QString str(const QVariantMap &m, const QString &k) { return m.value(k).toString(); }
} // namespace

QString Fmt::rate(double bps)
{
    if (bps < 1024)
        return QString::number(qRound64(bps)) + u" B/s"_s;
    if (bps < 1024.0 * 1024)
        return trimmedNumber(bps / 1024, 1) + u" KB/s"_s;
    if (bps < 1024.0 * 1024 * 1024)
        return trimmedNumber(bps / (1024.0 * 1024), 1) + u" MB/s"_s;
    return trimmedNumber(bps / (1024.0 * 1024 * 1024), 2) + u" GB/s"_s;
}

QString Fmt::mb(double v)
{
    if (v < 0 || std::isnan(v))
        return u"\u2014"_s;
    if (v >= 1024)
        return trimmedNumber(v / 1024, 1) + u" GB"_s;
    return QString::number(qRound64(v)) + u" MB"_s;
}

QString Fmt::duration(double ms)
{
    if (ms < 0 || std::isnan(ms))
        return {};
    if (ms < 1000)
        return QString::number(qRound64(ms)) + u" ms"_s;
    const qint64 total = qRound64(ms / 1000);
    if (total < 60) {
        const double s = ms / 1000;
        return (s < 10 ? trimmedNumber(s, 1) : QString::number(qRound64(s))) + u" s"_s;
    }
    return QString::number(total / 60) + u"m "_s + QString::number(total % 60) + u"s"_s;
}

QString Fmt::tokens(const QVariantMap &ev)
{
    if (!isNum(ev.value(u"tokens_out"_s)))
        return {};
    QString out = QString::number(qRound64(ev.value(u"tokens_out"_s).toDouble())) + u" tok"_s;
    if (isNum(ev.value(u"token_rate"_s)))
        out += u" · "_s + trimmedNumber(ev.value(u"token_rate"_s).toDouble(), 1) + u" tok/s"_s;
    return out;
}

QString Fmt::relative(double ts, double now)
{
    if (ts <= 0)
        return u"never"_s;
    const double age = std::max(0.0, now - ts);
    if (age < 60)
        return trimmedNumber(age, 0) + u"s ago"_s;
    if (age < 3600)
        return trimmedNumber(age / 60, 0) + u"m ago"_s;
    if (age < 86400)
        return trimmedNumber(age / 3600, 1) + u"h ago"_s;
    return trimmedNumber(age / 86400, 1) + u"d ago"_s;
}

QString Fmt::eventLabel(const QString &e)
{
    static const QHash<QString, QString> labels{
        {u"model.load"_s, u"Loaded"_s},    {u"model.unload"_s, u"Unloaded"_s},
        {u"model.evict"_s, u"Evicted"_s},  {u"model.shrink"_s, u"Shrunk"_s},
        {u"model.route"_s, u"Routed"_s},   {u"request.start"_s, u"Request"_s},
        {u"request.finish"_s, u"Completed"_s}};
    return labels.value(e, e);
}

int Fmt::eventTone(const QString &e)
{
    if (e == u"model.load"_s || e == u"request.finish"_s)
        return 1;
    if (e == u"model.shrink"_s)
        return 2;
    if (e == u"model.evict"_s)
        return 3;
    if (e == u"model.route"_s || e == u"request.start"_s)
        return 4;
    return 0;
}

QString Fmt::modelKind(const QString &model)
{
    const QString n = model.toLower();
    auto any = [&n](std::initializer_list<const char *> keys) {
        for (const char *k : keys)
            if (n.contains(QLatin1StringView(k)))
                return true;
        return false;
    };
    if (any({"embed", "bge", "e5-", "nomic", "gte"}))
        return u"vector"_s;
    if (any({"sd", "stable-diffusion", "sdxl", "flux", "dreamshaper", "lcm", "pixart"}))
        return u"image"_s;
    if (any({"whisper", "stt", "tts", "kokoro", "piper", "bark"}))
        return u"audio"_s;
    if (any({"vision", "llava", "moondream", "blip"}))
        return u"vision"_s;
    if (any({"code", "deepseek", "coder", "codellama"}))
        return u"code"_s;
    return u"brain"_s;
}

QString Fmt::shortCpuName(const QString &model)
{
    if (model.isEmpty())
        return {};
    QString s = model;
    s.remove(QRegularExpression(u"\\((R|TM|r|tm)\\)"_s));
    s.remove(QRegularExpression(u"\\s+CPU\\s+@.*$"_s, QRegularExpression::CaseInsensitiveOption));
    s.remove(QRegularExpression(u"\\s+@\\s+.*$"_s));
    return s.simplified();
}

QString Fmt::hardwareLabel(const QString &kind, const QVariantMap &hw)
{
    if (kind == u"cpu"_s) {
        const QVariantMap cpu = hw.value(u"cpu"_s).toMap();
        if (!str(cpu, u"soc"_s).isEmpty())
            return str(cpu, u"soc"_s).toUpper();
        const QString m = shortCpuName(str(cpu, u"model"_s));
        if (!m.isEmpty())
            return m;
        return str(cpu, u"arch"_s) == u"aarch64"_s ? u"ARM64"_s : u"CPU"_s;
    }
    if (kind == u"gpu"_s) {
        const QVariantMap gpu = hw.value(u"gpu"_s).toMap();
        const QString type = str(gpu, u"type"_s);
        if (type.isEmpty() || type == u"none"_s)
            return {};
        const double vram = gpu.value(u"vram_mb"_s).toDouble();
        const QString vramText = vram > 0 ? u" "_s + QString::number(qRound64(vram / 1024)) + u"GB"_s : QString();
        if (type == u"nvidia"_s) {
            QString m = str(gpu, u"model"_s);
            m.remove(QRegularExpression(u"NVIDIA\\s*GeForce\\s*"_s, QRegularExpression::CaseInsensitiveOption));
            m.remove(QRegularExpression(u"^NVIDIA\\s*"_s, QRegularExpression::CaseInsensitiveOption));
            m = m.trimmed();
            return u"NVIDIA "_s + (m.isEmpty() ? u"GPU"_s : m) + vramText;
        }
        if (type == u"amd"_s)
            return u"AMD "_s + (str(gpu, u"model"_s).isEmpty() ? u"GPU"_s : str(gpu, u"model"_s)) + vramText;
        if (type == u"apple"_s)
            return str(gpu, u"model"_s).isEmpty() ? u"Apple Silicon"_s : str(gpu, u"model"_s);
        if (type == u"mali"_s)
            return str(gpu, u"model"_s).isEmpty() ? u"Mali GPU"_s : str(gpu, u"model"_s);
        return str(gpu, u"model"_s).isEmpty() ? type.toUpper() : str(gpu, u"model"_s);
    }
    if (kind == u"npu"_s) {
        const QVariantMap npu = hw.value(u"npu"_s).toMap();
        const QString type = str(npu, u"type"_s);
        if (type.isEmpty() || type == u"none"_s)
            return {};
        if (type == u"rknpu"_s) {
            const double tops = npu.value(u"tops"_s).toDouble();
            return u"RK3588"_s + (tops > 0 ? u" · "_s + QString::number(tops, 'g', 4) + u" TOPS"_s : QString());
        }
        if (!str(npu, u"device"_s).isEmpty())
            return str(npu, u"device"_s).toUpper();
        return type.toUpper();
    }
    return {};
}

QString Fmt::systemLine(const QVariantMap &hw)
{
    QString out = str(hw, u"board"_s);
    const QVariantMap cpu = hw.value(u"cpu"_s).toMap();
    if (out.isEmpty())
        out = str(cpu, u"model"_s);
    if (out.isEmpty())
        out = u"System"_s;
    if (!str(cpu, u"arch"_s).isEmpty())
        out += u" · "_s + str(cpu, u"arch"_s);
    if (hw.value(u"ram_mb"_s).toDouble() > 0)
        out += u" · "_s + QString::number(qRound64(hw.value(u"ram_mb"_s).toDouble() / 1024)) + u" GB RAM"_s;
    return out;
}

QString Fmt::coreLabel(const QVariantMap &core)
{
    QString out = u"C"_s + QString::number(core.value(u"core"_s).toInt());
    const double khz = core.value(u"freq_khz"_s).toDouble();
    if (khz > 0)
        out += u" "_s + trimmedNumber(khz / 1000000, 1) + u"G"_s;
    return out;
}

QString Fmt::backendName(const QString &name)
{
    static const QRegularExpression re(u"^([^@]+)@https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\]|0\\.0\\.0\\.0):(\\d+)$"_s);
    const auto m = re.match(name);
    return m.hasMatch() ? m.captured(1) + u":"_s + m.captured(2) : name;
}

int Fmt::heartbeatState(double hb, double now)
{
    if (hb <= 0)
        return 3;
    const double age = now - hb;
    return age < 60 ? 0 : age < 300 ? 1 : 2;
}

QString Fmt::stateLabel(int s)
{
    switch (s) {
    case 0: return u"online"_s;
    case 1: return u"stale"_s;
    case 2: return u"offline"_s;
    default: return u"unknown"_s;
    }
}

QString Fmt::percent(double v)
{
    return QString::number(qRound(std::clamp(v, 0.0, 100.0))) + u"%"_s;
}

QVariantList Fmt::gpus(const QVariantMap &act)
{
    QVariantList src;
    const QVariantMap gpuBlock = act.value(u"gpu"_s).toMap();
    if (act.value(u"gpus"_s).typeId() == QMetaType::QVariantList)
        src = act.value(u"gpus"_s).toList();
    else if (gpuBlock.value(u"devices"_s).typeId() == QMetaType::QVariantList)
        src = gpuBlock.value(u"devices"_s).toList();
    else if (!gpuBlock.isEmpty())
        src << gpuBlock;

    const QVariantMap hw = act.value(u"hardware"_s).toMap();
    const bool single = !(act.value(u"gpus"_s).typeId() == QMetaType::QVariantList
                          || gpuBlock.value(u"devices"_s).typeId() == QMetaType::QVariantList);
    QVariantList out;
    for (int i = 0; i < src.size(); ++i) {
        const QVariantMap g = src.at(i).toMap();
        const QString type = str(g, u"type"_s);
        if (single && (type.isEmpty() || type == u"none"_s))
            continue; // the web app hides the card when no GPU is present
        QVariantMap n;
        n[u"index"_s] = i;
        n[u"type"_s] = type;
        QString label;
        if (single) {
            label = Fmt::hardwareLabel(u"gpu"_s, hw);
        } else {
            QString model = str(g, u"model"_s);
            if (model.isEmpty())
                model = str(g, u"name"_s);
            label = model.isEmpty() ? type.toUpper() : model;
        }
        if (label.isEmpty())
            label = type.isEmpty() ? u"GPU"_s : type.toUpper();
        n[u"label"_s] = label;
        const QVariantMap load = g.value(u"load"_s).toMap();
        auto num = [](const QVariant &v) { return isNum(v) ? v.toDouble() : -1.0; };
        double lp = num(load.value(u"load_percent"_s));
        if (lp < 0)
            lp = num(g.value(u"load_percent"_s));
        n[u"loadPercent"_s] = lp;
        n[u"freqHz"_s] = num(load.value(u"freq_hz"_s));
        n[u"vramPercent"_s] = num(g.value(u"vram_percent"_s));
        n[u"vramUsedMb"_s] = num(g.value(u"vram_used_mb"_s));
        n[u"vramTotalMb"_s] = num(g.value(u"vram_total_mb"_s));
        out << n;
    }
    return out;
}

QVariantList Fmt::mergeLoaded(const QVariantList &local, const QVariantList &workers)
{
    QVariantList out;
    for (const QVariant &v : local) {
        QVariantMap m = v.toMap();
        if (str(m, u"host"_s).isEmpty())
            m[u"host"_s] = u"controller"_s;
        out << m;
    }
    for (const QVariant &wv : workers) {
        const QVariantMap w = wv.toMap();
        for (const QVariant &bv : w.value(u"backends"_s).toList()) {
            const QVariantMap b = bv.toMap();
            QString bn = str(b, u"name"_s);
            if (bn.isEmpty())
                bn = str(b, u"type"_s);
            if (bn.isEmpty())
                bn = u"backend"_s;
            const QString backend = Fmt::backendName(bn);
            for (const QVariant &mv : b.value(u"loaded_models"_s).toList()) {
                QVariantMap row;
                row[u"backend"_s] = backend;
                row[u"host"_s] = str(w, u"name"_s);
                if (mv.typeId() == QMetaType::QString) {
                    row[u"name"_s] = mv.toString();
                    row[u"purpose"_s] = QString();
                } else if (mv.typeId() == QMetaType::QVariantMap) {
                    const QVariantMap m = mv.toMap();
                    QString name = str(m, u"name"_s);
                    if (name.isEmpty())
                        name = str(m, u"id"_s);
                    row[u"name"_s] = name.isEmpty() ? u"model"_s : name;
                    QString purpose = str(m, u"purpose"_s);
                    if (purpose.isEmpty())
                        purpose = str(m, u"capability"_s);
                    row[u"purpose"_s] = purpose;
                    for (const QString &k : {u"size_mb"_s, u"vram_mb"_s, u"ram_mb"_s})
                        if (isNum(m.value(k)))
                            row[k] = m.value(k);
                } else {
                    continue;
                }
                out << row;
            }
        }
    }
    return out;
}

} // namespace Taos::Activity
