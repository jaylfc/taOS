#pragma once
#include "taosqt/global.h"

namespace Taos {
/// Registers the Taos QML types (module `Taos` 1.0: Session, Client,
/// EventStream, LiveQuery, JsonListModel as uncreatable types, and the
/// TaosError enum) so QML can use their enums and properties. Needs the library to
/// be built with Qt Qml (TAOSQT_WITH_QML); a no-op declaration otherwise.
TAOSQT_EXPORT void registerQmlTypes();
} // namespace Taos
