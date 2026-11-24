// SPDX-License-Identifier: AGPL-3.0-or-later
// The pure rules: formatting, labels, GPU normalising, loaded-model merge.
#include <QtTest>

#include "fmt.h"

using namespace Qt::StringLiterals;
using namespace Taos::Activity;

// Test JSON is written with single quotes (moc cannot read raw strings).
static QByteArray fix(QByteArray j) { return j.replace('\'', '"'); }
static QVariantMap map(const QByteArray &json) { return QJsonDocument::fromJson(fix(json)).object().toVariantMap(); }

class TstFmt : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void rates()
    {
        QCOMPARE(Fmt::rate(12), u"12 B/s"_s);
        QCOMPARE(Fmt::rate(3482), u"3.4 KB/s"_s);
        QCOMPARE(Fmt::rate(1.5 * 1024 * 1024), u"1.5 MB/s"_s);
        QCOMPARE(Fmt::rate(1.25 * 1024 * 1024 * 1024), u"1.25 GB/s"_s);
    }
    void sizes()
    {
        QCOMPARE(Fmt::mb(512), u"512 MB"_s);
        QCOMPARE(Fmt::mb(1536), u"1.5 GB"_s);
        QCOMPARE(Fmt::mb(-1), u"\u2014"_s);
    }
    void durations()
    {
        QCOMPARE(Fmt::duration(12), u"12 ms"_s);
        QCOMPARE(Fmt::duration(3400), u"3.4 s"_s);
        QCOMPARE(Fmt::duration(25000), u"25 s"_s);
        QCOMPARE(Fmt::duration(125000), u"2m 5s"_s);
        QCOMPARE(Fmt::duration(59600), u"1m 0s"_s); // rounds before splitting: never "59m 60s"
        QCOMPARE(Fmt::duration(-1), QString());
    }
    void tokens()
    {
        QCOMPARE(Fmt::tokens(map("{'tokens_out':84,'token_rate':33.64}")), u"84 tok · 33.6 tok/s"_s);
        QCOMPARE(Fmt::tokens(map("{'tokens_out':5}")), u"5 tok"_s);
        QCOMPARE(Fmt::tokens(map("{'tokens_out':null}")), QString());
    }
    void relative()
    {
        QCOMPARE(Fmt::relative(100, 103), u"3s ago"_s);
        QCOMPARE(Fmt::relative(100, 220), u"2m ago"_s);
        QCOMPARE(Fmt::relative(0, 1), u"never"_s);
    }
    void eventLabels()
    {
        QCOMPARE(Fmt::eventLabel(u"model.load"_s), u"Loaded"_s);
        QCOMPARE(Fmt::eventLabel(u"request.finish"_s), u"Completed"_s);
        QCOMPARE(Fmt::eventLabel(u"model.teleport"_s), u"model.teleport"_s);
        QCOMPARE(Fmt::eventTone(u"model.evict"_s), 3);
    }
    void modelKinds()
    {
        QCOMPARE(Fmt::modelKind(u"bge-m3"_s), u"vector"_s);
        QCOMPARE(Fmt::modelKind(u"whisper-small"_s), u"audio"_s);
        QCOMPARE(Fmt::modelKind(u"qwen3:4b"_s), u"brain"_s);
    }
    void hardwareLabels()
    {
        const QVariantMap rk = map("{'cpu':{'soc':'rk3588','model':'Cortex-A76','arch':'aarch64'}, 'npu':{'type':'rknpu','tops':6},'gpu':{'type':'none'}}");
        QCOMPARE(Fmt::hardwareLabel(u"cpu"_s, rk), u"RK3588"_s);
        QCOMPARE(Fmt::hardwareLabel(u"npu"_s, rk), u"RK3588 · 6 TOPS"_s);
        QCOMPARE(Fmt::hardwareLabel(u"gpu"_s, rk), QString());
        const QVariantMap pc = map("{'cpu':{'model':'Intel(R) Core(TM) i5-10600 CPU @ 3.30GHz','arch':'x86_64'}, 'gpu':{'type':'nvidia','model':'NVIDIA GeForce RTX 3090','vram_mb':24576}}");
        QCOMPARE(Fmt::hardwareLabel(u"cpu"_s, pc), u"Intel Core i5-10600"_s);
        QCOMPARE(Fmt::hardwareLabel(u"gpu"_s, pc), u"NVIDIA RTX 3090 24GB"_s);
        QCOMPARE(Fmt::hardwareLabel(u"cpu"_s, map("{'cpu':{'arch':'aarch64'}}")), u"ARM64"_s);
        QCOMPARE(Fmt::hardwareLabel(u"cpu"_s, map("{}")), u"CPU"_s);
    }
    void systemLine()
    {
        QCOMPARE(Fmt::systemLine(map("{'board':'rk3588','cpu':{'arch':'aarch64'},'ram_mb':16384}")),
                 u"rk3588 · aarch64 · 16 GB RAM"_s);
    }
    void workerStates()
    {
        QCOMPARE(Fmt::heartbeatState(1000, 1030), 0);
        QCOMPARE(Fmt::heartbeatState(1000, 1100), 1);
        QCOMPARE(Fmt::heartbeatState(1000, 1400), 2);
        QCOMPARE(Fmt::heartbeatState(0, 1400), 3);
        QCOMPARE(Fmt::backendName(u"ollama@http://localhost:11434"_s), u"ollama:11434"_s);
    }
    void gpusOneBecomesAList()
    {
        const auto l = Fmt::gpus(map("{'hardware':{'gpu':{'type':'nvidia','model':'NVIDIA GeForce GTX 1050 Ti','vram_mb':4096}}, 'gpu':{'type':'nvidia','load':{'load_percent':23,'freq_hz':8e8},'vram_percent':40,'vram_used_mb':1600,'vram_total_mb':4096}}"));
        QCOMPARE(l.size(), 1);
        QCOMPARE(l[0].toMap().value(u"label"_s).toString(), u"NVIDIA GTX 1050 Ti 4GB"_s);
        QCOMPARE(l[0].toMap().value(u"loadPercent"_s).toDouble(), 23.0);
    }
    void gpusNoneIsHidden()
    {
        QCOMPARE(Fmt::gpus(map("{'gpu':{'type':'none','load':{}}}")).size(), 0);
        QCOMPARE(Fmt::gpus(map("{}")).size(), 0);
    }
    void gpusArrayShowsEvery()
    {
        const auto l = Fmt::gpus(map("{'gpus':[ {'type':'nvidia','model':'NVIDIA GeForce RTX 3090','load':{'load_percent':41},'vram_percent':62.5,'vram_used_mb':15360,'vram_total_mb':24576}, {'type':'nvidia','model':'NVIDIA GeForce RTX 3060','load':{'load_percent':7}}, {'type':'intel','model':'Intel UHD','load':null}]}"));
        QCOMPARE(l.size(), 3);
        QCOMPARE(l[1].toMap().value(u"label"_s).toString(), u"NVIDIA GeForce RTX 3060"_s);
        QCOMPARE(l[1].toMap().value(u"vramPercent"_s).toDouble(), -1.0);   // unknown, not zero
        QCOMPARE(l[2].toMap().value(u"loadPercent"_s).toDouble(), -1.0);
        const auto d = Fmt::gpus(map("{'gpu':{'devices':[{'type':'amd','model':'A'},{'type':'amd','model':'B'}]}}"));
        QCOMPARE(d.size(), 2);
    }
    void mergeLoaded()
    {
        const QVariantList local = QJsonDocument::fromJson(fix("[{'name':'qwen','backend':'ollama'}]")).array().toVariantList();
        const QVariantList workers = QJsonDocument::fromJson(fix("[{'name':'pc','backends':[ {'name':'ollama@http://localhost:11434','loaded_models':['llama3',{'name':'Qwen','purpose':'chat','vram_mb':4200}]}, {'type':'vllm','loaded_models':null}]},{'name':'x','backends':null}]")).array().toVariantList();
        const auto m = Fmt::mergeLoaded(local, workers);
        QCOMPARE(m.size(), 3);
        QCOMPARE(m[0].toMap().value(u"host"_s).toString(), u"controller"_s);
        QCOMPARE(m[1].toMap().value(u"name"_s).toString(), u"llama3"_s);
        QCOMPARE(m[1].toMap().value(u"host"_s).toString(), u"pc"_s);
        QCOMPARE(m[1].toMap().value(u"backend"_s).toString(), u"ollama:11434"_s);
        QCOMPARE(m[2].toMap().value(u"vram_mb"_s).toInt(), 4200);
    }
};

QTEST_GUILESS_MAIN(TstFmt)
#include "tst_fmt.moc"
