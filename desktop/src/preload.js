'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// The shell page's whole surface to the main process. Service pages get no
// preload at all, so nothing here is reachable from a website.
contextBridge.exposeInMainWorld('hole', {
  state: () => ipcRenderer.invoke('app:state'),
  show: (id) => ipcRenderer.invoke('stage:show', id),
  bounds: (rect) => ipcRenderer.send('stage:bounds', rect),
  overlay: (on) => ipcRenderer.send('stage:overlay', on),
  nav: (action, arg) => ipcRenderer.invoke('nav', action, arg),
  capture: () => ipcRenderer.invoke('capture:run'),
  scan: () => ipcRenderer.invoke('scan:run'),
  send: (items) => ipcRenderer.invoke('ingest:send', items),
  runStart: () => ipcRenderer.invoke('pipeline:start'),
  runStatus: () => ipcRenderer.invoke('pipeline:status'),
  saveSettings: (values) => ipcRenderer.invoke('settings:save', values),
  setServiceUrl: (id, url) => ipcRenderer.invoke('services:setUrl', id, url),
  onNav: (callback) => ipcRenderer.on('nav:event', (_event, info) => callback(info)),
});
