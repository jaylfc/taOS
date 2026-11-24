// SPDX-License-Identifier: AGPL-3.0-or-later
#include "types.h"

#include <QtQml/qqml.h>

#include "engine.h"
#include "feed.h"
#include "fmt.h"

namespace Taos::Activity {
void registerTypes()
{
    static bool done = false;
    if (done)
        return;
    done = true;
    const char *uri = "Taos.Activity";
    qmlRegisterType<ActivityEngine>(uri, 1, 0, "ActivityEngine");
    qmlRegisterType<ModelActivityFeed>(uri, 1, 0, "ModelActivityFeed");
    qmlRegisterSingletonType<Fmt>(uri, 1, 0, "Fmt", [](QQmlEngine *, QJSEngine *) { return new Fmt; });
}
} // namespace Taos::Activity
