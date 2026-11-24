// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "taosqt/global.h"

namespace Taos {
/// Registers the Taos QML types (module `Taos` 1.0: Session, Client,
/// EventStream, JsonListModel as uncreatable types, LiveQuery as a creatable type, and the
/// TaosError enum) so QML can use their enums and properties. Needs the library to
/// be built with Qt Qml (TAOSQT_WITH_QML); a no-op declaration otherwise.
TAOSQT_EXPORT void registerQmlTypes();
} // namespace Taos
