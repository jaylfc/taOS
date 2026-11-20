#include "watchdog.h"

#include <chrono>
#include <cstdio>
#include <unistd.h>

static long long nowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

Watchdog::Watchdog(int limitMs) : m_limit(limitMs) { m_thread = std::thread([this] { run(); }); }

Watchdog::~Watchdog() {
    { std::lock_guard<std::mutex> l(m_mu); m_quit = true; }
    m_cv.notify_all();
    m_thread.join();
}

void Watchdog::setActive(bool on) {
    beat();
    { std::lock_guard<std::mutex> l(m_mu); m_active = on; }
    m_cv.notify_all();
}

void Watchdog::beat() { m_last = nowMs(); }

void Watchdog::run() {
    std::unique_lock<std::mutex> l(m_mu);
    while (!m_quit) {
        m_cv.wait(l, [this] { return m_active || m_quit; });
        while (m_active && !m_quit) {
            m_cv.wait_for(l, std::chrono::milliseconds(m_limit / 3));
            if (m_active && !m_quit && nowMs() - m_last > m_limit) {
                fprintf(stderr, "taos-overview: the interface stopped responding while the overlay was up; exiting to release the keyboard\n");
                _exit(3);
            }
        }
    }
}
