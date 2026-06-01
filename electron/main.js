const { app, BrowserWindow, dialog } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

let mainWindow = null;
let serverProcess = null;
const BACKEND_PORT = 6969;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

function resourcePath(...segments) {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, ...segments);
  }
  return path.join(__dirname, '..', ...segments);
}

function getBackendDir() {
  if (app.isPackaged) {
    return process.resourcesPath;
  }
  return path.join(__dirname, '..');
}

function waitForServer(url, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    function poll() {
      const req = http.get(url, (res) => {
        resolve();
        req.destroy();
      });
      req.on('error', () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Server did not start within ${timeoutMs / 1000}s`));
        } else {
          setTimeout(poll, 500);
        }
      });
      req.end();
    }
    poll();
  });
}

function installPythonDeps(backendDir) {
  return new Promise((resolve, reject) => {
    const pip = spawn('python3', ['-m', 'pip', 'install', '-e', '.', '--quiet'], {
      cwd: backendDir,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    pip.stderr.on('data', (d) => { stderr += d.toString(); });
    pip.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`pip install failed (${code}): ${stderr.slice(-500)}`));
    });
    pip.on('error', reject);
  });
}

async function startServer() {
  const backendDir = getBackendDir();
  const dataDir = path.join(app.getPath('userData'), 'data');

  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
    const defaultData = path.join(backendDir, 'data');
    if (fs.existsSync(defaultData)) {
      execSync(`cp -r "${defaultData}/." "${dataDir}/"`, { stdio: 'ignore' });
    }
  }

  if (app.isPackaged) {
    try {
      await installPythonDeps(backendDir);
    } catch (err) {
      dialog.showErrorBox('Setup Error', `Failed to install dependencies:\n${err.message}`);
      throw err;
    }
  }

  serverProcess = spawn('python3', [
    '-m', 'uvicorn', 'tinyagentos.app:create_app', '--factory',
    '--host', '127.0.0.1',
    '--port', String(BACKEND_PORT),
    '--log-level', 'info',
  ], {
    cwd: backendDir,
    env: {
      ...process.env,
      TAOS_HOST: '127.0.0.1',
      TAOS_PORT: String(BACKEND_PORT),
      TAOS_DATA_DIR: dataDir,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  serverProcess.stdout.on('data', (d) => console.log(`[backend] ${d}`));
  serverProcess.stderr.on('data', (d) => console.error(`[backend] ${d}`));

  serverProcess.on('exit', (code) => {
    console.log(`[backend] exited with code ${code}`);
    serverProcess = null;
  });

  await waitForServer(`${BACKEND_URL}/api/health`);
}

function stopServer() {
  if (serverProcess) {
    serverProcess.kill('SIGTERM');
    setTimeout(() => {
      if (serverProcess) serverProcess.kill('SIGKILL');
    }, 5000);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'TinyAgentOS',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadURL(BACKEND_URL);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  try {
    await startServer();
    createWindow();
  } catch (err) {
    console.error('Startup failed:', err);
    if (!app.isPackaged) process.exit(1);
  }
});

app.on('window-all-closed', () => {
  stopServer();
  app.quit();
});

app.on('before-quit', () => {
  stopServer();
});

app.on('activate', () => {
  if (mainWindow === null && serverProcess) {
    createWindow();
  }
});
