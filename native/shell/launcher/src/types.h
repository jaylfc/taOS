#pragma once
#include <QList>
#include <QString>
#include <QStringList>
#include <atomic>
#include <memory>

namespace taos::search {

// One thing a result can do. `verb` says how the runner executes it:
//   launch-app   data = desktop file id, args = [action id] (optional)
//   focus-window data = window id
//   exec         args = argv, run detached
//   copy         data = text for the clipboard
//   open         data = path or URL, handed to xdg-open
//   ask          data = the question for the person's agent
//   complete     data = text to put in the search field (mention autocomplete)
//   send-mention args = agent ids, data = the message; id "send" or "send-open" (open the chat after)
//   ask-open     args = argv of a chat app to launch, data = the question (copied to the clipboard first)
//   stub         nothing happens yet; `label` explains (settings deep links, agent chat)
struct Action {
    QString id;
    QString label;
    QString verb;
    QStringList args;
    QString data;
    bool confirm = false;   // needs a second Enter ("Press Enter again to confirm")
    bool stub = false;
    QString hint;     // short right-aligned text when this is the primary action; label when empty
    QString shownHint() const { return hint.isEmpty() ? label : hint; }
};

struct Result {
    QString id;        // stable across queries: "app:firefox.desktop", "window:12", "cmd:lock"
    QString provider;
    QString kind;      // app window command calc file setting ask
    QString title;
    QString subtitle;
    QString icon;      // icon theme name, absolute path, or empty (the UI draws a monogram)
    double score = 0;  // higher is better; roughly 0..1.3
    QList<Action> actions;   // actions[0] is the primary one (Enter)
};

// Cooperative cancellation: a provider polls it inside loops that may run long.
class CancelToken {
public:
    CancelToken() : m_flag(std::make_shared<std::atomic_bool>(false)) {}
    bool cancelled() const { return m_flag->load(std::memory_order_relaxed); }
    void cancel() const { m_flag->store(true, std::memory_order_relaxed); }
private:
    std::shared_ptr<std::atomic_bool> m_flag;
};

}  // namespace taos::search
