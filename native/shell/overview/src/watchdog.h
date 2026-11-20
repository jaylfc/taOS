#pragma once
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>

// Last line of defence for the exclusive keyboard: while the overlay is up, the GUI thread must
// beat at least every few seconds; if it stops (a hang), the process exits so the compositor
// drops the surface and the keyboard comes back, and the session supervisor restarts the daemon.
// While the overlay is hidden the thread sleeps on a condition variable and costs nothing.
class Watchdog {
public:
    explicit Watchdog(int limitMs = 6000);
    ~Watchdog();
    void setActive(bool on);
    void beat();
private:
    void run();
    int m_limit;
    std::mutex m_mu;
    std::condition_variable m_cv;
    bool m_active = false, m_quit = false;
    std::atomic<long long> m_last { 0 };
    std::thread m_thread;
};
