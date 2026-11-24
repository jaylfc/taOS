// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QVariantList>

namespace TaosSettings {

// The taOS shell shortcuts, as the session's sway snippets bind them (native/shell/*/sway/*.conf and
// native/shell/*/*.conf). Read only for now: the table is the one place that lists them.
QVariantList shellShortcuts();

} // namespace TaosSettings
