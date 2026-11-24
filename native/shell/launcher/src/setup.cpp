#include "setup.h"

#include "providers_more.h"

namespace taos::search {

Installed installDefaultProviders(SearchEngine *engine, const Deps &d) {
    Installed in;
    in.frecency = std::make_shared<Frecency>(d.frecencyPath.isEmpty() ? Frecency::defaultPath() : d.frecencyPath);
    in.config = std::make_shared<LauncherConfig>(d.configPath.isEmpty() ? LauncherConfig::defaultPath() : d.configPath);
    in.apps = std::make_shared<AppsProvider>(in.frecency);
    auto apps = in.apps;
    engine->addProvider(apps);
    engine->addProvider(std::make_shared<WindowsProvider>(WindowsProvider::Fetch{}, [apps](const QString &id) { return apps->iconForAppId(id); }));
    engine->addProvider(std::make_shared<CommandsProvider>(std::function<bool(const QString &)>{}, in.frecency));
    engine->addProvider(std::make_shared<CalcProvider>());
    auto files = d.files ? d.files : std::make_shared<FileIndex>(FileIndex::defaultRoots(), FileIndex::defaultCachePath());
    engine->addProvider(std::make_shared<FilesProvider>(files));
    engine->addProvider(std::make_shared<SettingsProvider>());
    engine->addProvider(std::make_shared<MentionProvider>(d.agents ? d.agents : std::make_shared<UnavailableDirectory>(), in.config, d.multiSend));
    engine->addProvider(std::make_shared<AskProvider>(in.config, [apps] { return apps->entries(); }));
    return in;
}

}  // namespace taos::search
