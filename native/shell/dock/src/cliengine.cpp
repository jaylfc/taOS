#include "cliengine.h"

#include <cstdio>

CliEngine::CliEngine(const QString &program, QObject *parent) : WindowEngine(parent), m_prog(program) {
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(20);
    connect(&m_debounce, &QTimer::timeout, this, &CliEngine::refresh);
}

CliEngine::~CliEngine() {
    if (m_sub) { m_sub->disconnect(this); m_sub->terminate(); m_sub->waitForFinished(500); }
}

void CliEngine::start() {
    startSubscribe();
    refresh();
}

void CliEngine::startSubscribe() {
    if (m_sub) { m_sub->deleteLater(); m_sub = nullptr; }
    m_sub = new QProcess(this);
    connect(m_sub, &QProcess::readyReadStandardOutput, this, [this] { m_sub->readAllStandardOutput(); m_debounce.start(); });
    connect(m_sub, &QProcess::finished, this, [this] { QTimer::singleShot(1000, this, &CliEngine::startSubscribe); });
    m_sub->start(m_prog, {QStringLiteral("subscribe")});
}

void CliEngine::refresh() {
    auto *p = new QProcess(this);
    connect(p, &QProcess::finished, this, [this, p] {
        QVector<WindowInfo> w;
        QString err;
        if (p->exitStatus() == QProcess::NormalExit && p->exitCode() == 0 && WindowJson::parseList(p->readAllStandardOutput(), &w, &err)) {
            if (!m_have || w != m_windows) { m_have = true; m_windows = w; emit windowsChanged(); }
        } else {
            fprintf(stderr, "taos-dock: %s list failed\n", qPrintable(m_prog));
        }
        p->deleteLater();
    });
    connect(p, &QProcess::errorOccurred, this, [p](QProcess::ProcessError) { p->deleteLater(); });
    p->start(m_prog, {QStringLiteral("list"), QStringLiteral("--json")});
}

void CliEngine::verb(const QString &v, int id) {
    auto *p = new QProcess(this);
    connect(p, &QProcess::finished, this, [this, p] { p->deleteLater(); m_debounce.start(); });
    connect(p, &QProcess::errorOccurred, this, [p](QProcess::ProcessError) { p->deleteLater(); });
    p->start(m_prog, {v, QString::number(id)});
}
