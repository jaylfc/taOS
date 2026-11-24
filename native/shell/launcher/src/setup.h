#pragma once
#include <memory>

#include "agents.h"
#include "config.h"
#include "engine.h"
#include "fileindex.h"
#include "frecency.h"
#include "providers.h"

namespace taos::search {

struct Deps {
    QString frecencyPath;                          // default: ~/.local/state/taos/launcher.json
    QString configPath;                            // default: ~/.config/taos/launcher.json
    std::shared_ptr<AgentDirectory> agents;        // default: UnavailableDirectory
    std::shared_ptr<FileIndex> files;              // default: the home folder, cache in ~/.cache/taos
    bool multiSend = false;                        // the ChatSender takes several recipients
};
struct Installed {
    std::shared_ptr<Frecency> frecency;
    std::shared_ptr<LauncherConfig> config;
    std::shared_ptr<AppsProvider> apps;
};

// The stock provider set, in the order the launcher and taos-search use.
Installed installDefaultProviders(SearchEngine *engine, const Deps &deps = {});

}  // namespace taos::search
