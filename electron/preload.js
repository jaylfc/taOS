const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('taos', {
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
  },
});
