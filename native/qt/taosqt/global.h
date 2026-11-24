// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QtCore/qglobal.h>

#if defined(TAOSQT_BUILD)
#  define TAOSQT_EXPORT Q_DECL_EXPORT
#else
#  define TAOSQT_EXPORT Q_DECL_IMPORT
#endif
