// The parts that cannot be Rust: LayerShellQt (C++-only API with no C ABI),
// QQuickWindow signal hooks, and qVersion()/RHI name lookups.
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QSGRendererInterface>
#include <QTimer>
#include <LayerShellQt/Window>
#include <atomic>
#include <cstdio>
#include <ctime>

static qint64 now_ns()
{
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return qint64(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

static const char *rhiName(QSGRendererInterface::GraphicsApi api)
{
    switch (api) {
    case QSGRendererInterface::OpenGL: return "opengl";
    case QSGRendererInterface::Vulkan: return "vulkan";
    case QSGRendererInterface::Software: return "software";
    case QSGRendererInterface::Direct3D11: return "d3d11";
    case QSGRendererInterface::Direct3D12: return "d3d12";
    case QSGRendererInterface::Metal: return "metal";
    case QSGRendererInterface::Null: return "null";
    default: return "unknown";
    }
}

class FrameProbe : public QObject
{
public:
    FrameProbe(QQuickWindow *w, qint64 t0ns, bool haveT0)
        : QObject(w), m_t0(t0ns), m_haveT0(haveT0)
    {
        const QByteArray path = qgetenv("POC_FRAMELOG");
        if (!path.isEmpty())
            m_log = std::fopen(path.constData(), "a");
        auto printRenderer = [this, w] {
            if (!m_rendererPrinted.exchange(true))
                std::fprintf(stderr, "renderer=%s\n",
                             rhiName(w->rendererInterface()->graphicsApi()));
        };
        connect(w, &QQuickWindow::sceneGraphInitialized, this, printRenderer, Qt::DirectConnection);
        connect(w, &QQuickWindow::frameSwapped, this, [this, printRenderer] {
            printRenderer();
            const qint64 t = now_ns();
            if (m_last == 0) {
                if (m_haveT0)
                    std::fprintf(stderr, "coldstart_ms=%.3f\n", (t - m_t0) / 1e6);
                else
                    std::fprintf(stderr, "coldstart_ms=na\n");
            } else if (m_log) {
                std::fprintf(m_log, "%lld\n", (long long)((t - m_last) / 1000));
                std::fflush(m_log);
            }
            m_last = t;
        }, Qt::DirectConnection);
    }
    ~FrameProbe() override { if (m_log) std::fclose(m_log); }
    bool rendererPrinted() const { return m_rendererPrinted; }
private:
    qint64 m_t0 = 0, m_last = 0;
    bool m_haveT0 = false;
    FILE *m_log = nullptr;
    std::atomic<bool> m_rendererPrinted{false};
};

static FrameProbe *g_probe = nullptr;

extern "C" int taos_shell_attach(void *enginePtr)
{
    auto *engine = static_cast<QQmlApplicationEngine *>(enginePtr);
    bool haveT0 = false;
    const qint64 t0 = qEnvironmentVariable("POC_T0_NS").toLongLong(&haveT0);
    std::fprintf(stderr, "toolkit=qt %s\n", qVersion());

    if (!engine || engine->rootObjects().size() != 2)
        return 1;
    auto *bar = qobject_cast<QQuickWindow *>(engine->rootObjects().at(0));
    auto *win = qobject_cast<QQuickWindow *>(engine->rootObjects().at(1));
    if (!bar || !win)
        return 1;

    if (QGuiApplication::platformName().startsWith(QLatin1String("wayland"))) {
        using LSW = LayerShellQt::Window;
        LSW *ls = LSW::get(bar);   // must precede bar->show()
        ls->setLayer(LSW::LayerTop);
        ls->setAnchors(LSW::Anchors(LSW::AnchorTop) | LSW::AnchorLeft | LSW::AnchorRight);
        ls->setExclusiveZone(48);
        ls->setScope(QStringLiteral("taos-poc-bar"));
        ls->setKeyboardInteractivity(LSW::KeyboardInteractivityNone);
    }
    bar->setHeight(48);
    bar->show();

    g_probe = new FrameProbe(win, t0, haveT0);
    win->show();

    bool ok = false;
    const int quitMs = qEnvironmentVariable("POC_AUTOQUIT_MS").toInt(&ok);
    if (ok && quitMs > 0)
        QTimer::singleShot(quitMs, QCoreApplication::instance(), &QCoreApplication::quit);
    return 0;
}

extern "C" void taos_shell_finish()
{
    if (!g_probe || !g_probe->rendererPrinted())
        std::fprintf(stderr, "renderer=none\n");
}
