const {app, BrowserWindow, ipcMain, nativeImage, session, shell} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const profileFile = path.join(__dirname, 'profile.json');
const profile = fs.existsSync(profileFile) ? JSON.parse(fs.readFileSync(profileFile, 'utf8')) : {
  name: 'Community Console', personal: false, templates: []
};
app.setName(profile.name);

const smoke = process.argv.includes('--smoke-test');
const setupPortal = process.argv.includes('--setup-portal');
const testDirectory = smoke && process.env.SIERRA_TEST_DATA_DIR;
const appData = app.getPath('appData');
const fallback = testDirectory || path.join(appData, profile.name);
const legacyName = 'Sierra Steward Community';
const legacyDirectory = path.join(appData, legacyName);
const hasSettings = directory => ['.env', 'branding.json', 'data'].some(file => fs.existsSync(path.join(directory, file)));
const dataDirectory = !smoke && !hasSettings(fallback) && hasSettings(legacyDirectory) ? legacyDirectory : fallback;
fs.mkdirSync(fallback, {recursive: true});
const portalData = setupPortal ? fs.mkdtempSync(path.join(app.getPath('temp'), 'community-console-portal-')) : '';
app.setPath('userData', portalData || fallback);

const executable = process.env.SIERRA_WORKER_EXE || path.join(process.resourcesPath, 'SierraWorker.exe');
const workerEnvironment = {...process.env, SIERRA_CONFIG_DIR: dataDirectory, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1'};
const workerOptions = {cwd: dataDirectory, env: workerEnvironment, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']};
const brandingFile = path.join(dataDirectory, 'branding.json');
let screen;
let portal;
let developerSession;
let worker;
let phase = 'Stopped';
let stopPromise;
let quitting = false;
const actionNames = new Set(['load', 'save', 'check', 'preview', 'channels', 'publish', 'member-preview', 'tickets', 'ticket-panel']);

function loadBranding() {
  try {
    const branding = validateBranding(JSON.parse(fs.readFileSync(brandingFile, 'utf8')));
    if (branding.name === legacyName) branding.name = profile.name;
    return branding;
  } catch {
    return {name: profile.name, logo: ''};
  }
}

function validateBranding(payload) {
  const name = payload?.name?.trim();
  const logo = payload?.logo || '';
  if (!name || name.length < 2 || name.length > 32) throw new Error('Enter a name from 2 to 32 characters.');
  if (logo) {
    const image = /^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})$/.exec(logo);
    if (!image || image[2].length > 1_666_668) throw new Error('Choose a PNG or JPEG logo under 1.25 MB.');
    const buffer = Buffer.from(image[2], 'base64');
    const signature = image[1] === 'png' ? '89504e470d0a1a0a' : 'ffd8ff';
    if (buffer.length > 1_250_000 || !buffer.subarray(0, signature.length / 2).equals(Buffer.from(signature, 'hex'))) {
      throw new Error('Choose a PNG or JPEG logo under 1.25 MB.');
    }
  }
  return {name, logo};
}

function emit(name, data) {
  if (screen && !screen.isDestroyed()) screen.webContents.send(name, data);
}

function state(next) {
  phase = next;
  emit('status', next);
}

function runRequest(action, request) {
  const payload = JSON.stringify(request || {});
  if (Buffer.byteLength(payload) > 2_000_000) return Promise.resolve({error: 'Keep requests under 2 MB.'});
  return new Promise(resolve => {
    const child = spawn(executable, [action], workerOptions);
    let response = '';
    const timer = setTimeout(() => child.kill(), 30_000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', value => {
      response += value;
      if (response.length > 3_000_000) child.kill();
    });
    child.stderr.resume();
    child.stdin.on('error', () => {});
    child.on('error', () => resolve({error: 'The bundled bot worker could not start.'}));
    child.on('close', code => {
      clearTimeout(timer);
      try {
        if (code !== 0) throw new Error('worker failed');
        resolve(JSON.parse(response));
      } catch {
        resolve({error: 'The bot worker did not complete the request.'});
      }
    });
    child.stdin.end(payload, 'utf8');
  });
}

function start() {
  if (worker) return;
  const child = worker = spawn(executable, ['start'], workerOptions);
  let recent = '';
  state('Connecting');
  child.stdin.on('error', () => {});
  const output = text => {
    emit('activity', text);
    recent = (recent + text).slice(-1000);
    if (phase === 'Connecting' && recent.includes('Connected as ')) state('Online');
  };
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', output);
  }
  child.on('error', () => emit('activity', 'The bundled bot worker failed to start.\n'));
  child.on('close', code => {
    worker = undefined;
    state(code === 0 || phase === 'Stopping' ? 'Stopped' : 'Failed');
  });
}

function stop() {
  if (!worker) return Promise.resolve();
  if (stopPromise) return stopPromise;
  const child = worker;
  state('Stopping');
  stopPromise = new Promise(resolve => {
    const timer = setTimeout(() => child.kill(), 7000);
    child.once('close', () => {
      clearTimeout(timer);
      stopPromise = undefined;
      resolve();
    });
    child.stdin.end('stop\n');
  });
  return stopPromise;
}

function openDeveloperPortal() {
  if (portal && !portal.isDestroyed()) { portal.focus(); return; }
  if (!developerSession) {
    developerSession = session.fromPartition('discord-developer-portal', {cache: false});
    developerSession.setPermissionRequestHandler((_contents, _permission, answer) => answer(false));
    developerSession.on('will-download', (_event, item) => item.cancel());
  }
  portal = new BrowserWindow({title: 'Discord Developer Portal', width: 1080, height: 785,
    webPreferences: {session: developerSession, contextIsolation: true, nodeIntegration: false, sandbox: true}});
  portal.setMenuBarVisibility(false);
  const isDiscord = target => {
    try { const url = new URL(target); return url.origin === 'https://discord.com'; } catch { return false; }
  };
  portal.webContents.on('will-navigate', (event, url) => { if (!isDiscord(url)) event.preventDefault(); });
  portal.webContents.on('will-redirect', (event, url) => { if (!isDiscord(url)) event.preventDefault(); });
  portal.webContents.setWindowOpenHandler(({url}) => {
    if (isDiscord(url)) portal.loadURL(url).catch(() => {});
    return {action: 'deny'};
  });
  portal.loadURL('https://discord.com/developers/applications').catch(() => {});
}

if (setupPortal) {
  app.whenReady().then(openDeveloperPortal);
  app.on('window-all-closed', () => app.quit());
  app.on('quit', () => { try { fs.rmSync(portalData, {recursive: true, force: true}); } catch {} });
} else if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (screen && !screen.isDestroyed()) {
      if (screen.isMinimized()) screen.restore();
      screen.focus();
    }
  });
  app.whenReady().then(() => {
    fs.mkdirSync(dataDirectory, {recursive: true});
    screen = new BrowserWindow({title: profile.name, width: 1120, height: 820, minWidth: 780, minHeight: 620,
      icon: path.join(__dirname, 'logo.png'),
      backgroundColor: '#0e1724', show: false,
      webPreferences: {preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true}});
    screen.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
    screen.webContents.on('will-navigate', event => event.preventDefault());
    screen.webContents.session.setPermissionRequestHandler((_contents, _permission, respond) => respond(false));
    screen.once('ready-to-show', () => screen.show());
    screen.setMenuBarVisibility(false);
    screen.loadFile(path.join(__dirname, 'index.html'));
    if (!smoke && process.argv.includes('--autostart')) start();
    if (smoke) {
      screen.webContents.once('did-finish-load', async () => {
        try {
          const logo = 'data:image/png;base64,' + fs.readFileSync(path.join(__dirname, 'logo.png')).toString('base64');
          const passed = await screen.webContents.executeJavaScript(`(async () => {
            const info = await window.sierra.request('info');
            const stored = await window.sierra.request('load');
            if (info.value?.name !== ${JSON.stringify(profile.name)} || stored.value?.hasToken !== false) return false;
            const payload = {name: 'My Flight Sim Club', logo: ${JSON.stringify(logo)}};
            const branding = await window.sierra.request('save-branding', payload);
            if (info.value.personal) return !!branding.error && info.value.templates.length === 12;
            const reopened = await window.sierra.request('info');
            return reopened.value?.branding.name === payload.name && reopened.value?.branding.logo === payload.logo;
          })()`);
          app.exit(passed ? 0 : 1);
        } catch (error) {
          console.error(error.message);
          app.exit(1);
        }
      });
    }
  }).catch(error => {
    console.error(error.message);
    app.exit(1);
  });
  ipcMain.handle('sierra', async (event, action, payload) => {
    if (!screen || event.sender !== screen.webContents || event.senderFrame !== screen.webContents.mainFrame) {
      return {error: 'Invalid dashboard window.'};
    }
    if (action === 'info') return {value: {name: profile.name, personal: profile.personal, status: phase,
      templates: profile.templates, branding: profile.personal ? null : loadBranding()}};
    if (action === 'developer-portal') { openDeveloperPortal(); return {value: true}; }
    if (action === 'open-discord') {
      if (!/^https:\/\/discord\.com\/channels\/[0-9]+\/[0-9]+(?:\/[0-9]+)?$/.test(payload?.url || '')) {
        return {error: 'Invalid Discord link.'};
      }
      await shell.openExternal(payload.url);
      return {value: true};
    }
    if (!profile.personal && (action === 'save-branding' || action === 'apply-branding')) {
      try {
        const branding = validateBranding(payload);
        if (action === 'apply-branding') return runRequest('branding', {name: branding.name, logo: branding.logo.split(',')[1] || ''});
        const staged = brandingFile + '.tmp';
        fs.writeFileSync(staged, JSON.stringify(branding), 'utf8');
        fs.renameSync(staged, brandingFile);
        screen.setTitle(branding.name);
        screen.setIcon(branding.logo ? nativeImage.createFromDataURL(branding.logo) : path.join(__dirname, 'logo.png'));
        return {value: branding};
      } catch (error) {
        return {error: error.message};
      }
    }
    if (action === 'start') start();
    else if (action === 'stop') await stop();
    else if (action === 'restart') { await stop(); start(); }
    else if (actionNames.has(action)) return runRequest(action, payload);
    else return {error: 'Unknown dashboard action.'};
    return {value: phase};
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => {
    if (!worker) return;
    event.preventDefault();
    if (!quitting) {
      quitting = true;
      stop().finally(() => app.quit());
    }
  });
}
