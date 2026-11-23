const {contextBridge, ipcRenderer} = require('electron');

contextBridge.exposeInMainWorld('sierra', {
  request: (action, data) => ipcRenderer.invoke('sierra', action, data),
  onStatus: callback => { ipcRenderer.on('status', (_event, value) => callback(value)); },
  onActivity: callback => { ipcRenderer.on('activity', (_event, value) => callback(value)); },
});
