// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QString>

#include "types.h"

namespace taos::search {

// The provider interface. A provider is a plug-in class: the engine owns it, calls prepare() when
// the launcher is about to show, and calls query() on a worker thread for every keystroke.
//
//  - query() must be thread safe and must poll `cancel` in anything that can take long.
//  - An empty `text` asks for suggestions (frequent apps, recent windows); return nothing if the
//    provider has none.
//  - Results carry their own score; the engine applies the per-kind weight when it ranks.
//  - A provider that overruns timeBudgetMs() is cancelled and its late results are dropped; the
//    others are never held up.
class Provider {
public:
    virtual ~Provider() = default;
    virtual QString id() const = 0;
    virtual QString name() const = 0;
    virtual int timeBudgetMs() const { return 150; }
    // False when the provider cannot work here (a CLI is absent): the engine skips it quietly.
    virtual bool available() const { return true; }
    // Called on the main thread right before the launcher shows. Must return quickly; start
    // background refreshes instead of waiting for them.
    virtual void prepare() {}
    virtual QList<Result> query(const QString &text, const CancelToken &cancel) = 0;
};

}  // namespace taos::search
