#!/usr/bin/env node
// BossHunter Browser Runtime - local HTTP-to-CDP bridge for the user's Chrome session.
// Requires Chrome remote debugging and Node.js 22+ (or the ws module fallback).

import http from 'node:http';
import { URL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';

const PORT = parseInt(process.env.BOSSHUNTER_BROWSER_PROXY_PORT || process.env.CDP_PROXY_PORT || '3456', 10);
const ENABLE_PORT_GUARD = !['0', 'false', 'no'].includes(String(process.env.BOSSHUNTER_ENABLE_PORT_GUARD || 'true').toLowerCase());
const COMMON_PORTS = String(process.env.BOSSHUNTER_CHROME_PORTS || '9222,9229,9333')
  .split(',')
  .map((value) => parseInt(value.trim(), 10))
  .filter((value) => value > 0 && value < 65536);
const RUNTIME_NAME = 'bosshunter';

let ws = null;
let cmdId = 0;
const pending = new Map();
const sessions = new Map();
const portGuardedSessions = new Set();
let chromePort = null;
let chromeWsPath = null;
let chromeWsUrl = null;
let chromeProduct = null;
let chromeName = null;
let connectingPromise = null;

let WS;
if (typeof globalThis.WebSocket !== 'undefined') {
  WS = globalThis.WebSocket;
} else {
  try {
    WS = (await import('ws')).default;
  } catch {
    console.error('[BossHunter Browser Runtime] Node.js < 22 requires ws. Upgrade Node.js or install ws.');
    process.exit(1);
  }
}

function activePortFiles() {
  const home = os.homedir();
  const localAppData = process.env.LOCALAPPDATA || '';
  if (os.platform() === 'darwin') {
    return [
      path.join(home, 'Library/Application Support/Google/Chrome/DevToolsActivePort'),
      path.join(home, 'Library/Application Support/Google/Chrome Canary/DevToolsActivePort'),
      path.join(home, 'Library/Application Support/Chromium/DevToolsActivePort'),
    ];
  }
  if (os.platform() === 'linux') {
    return [
      path.join(home, '.config/google-chrome/DevToolsActivePort'),
      path.join(home, '.config/chromium/DevToolsActivePort'),
    ];
  }
  if (os.platform() === 'win32') {
    return [
      path.join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort'),
      path.join(localAppData, 'Chromium/User Data/DevToolsActivePort'),
    ];
  }
  return [];
}

function checkPort(port, host = '127.0.0.1', timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = net.createConnection(port, host);
    const timer = setTimeout(() => { socket.destroy(); resolve(false); }, timeoutMs);
    socket.once('connect', () => { clearTimeout(timer); socket.destroy(); resolve(true); });
    socket.once('error', () => { clearTimeout(timer); resolve(false); });
  });
}

async function getChromeVersion(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(2000) });
    if (!response.ok) return null;
    const data = await response.json();
    if (!data.webSocketDebuggerUrl) return null;
    return data;
  } catch {
    return null;
  }
}

async function discoverChromePort() {
  for (const filePath of activePortFiles()) {
    try {
      const lines = fs.readFileSync(filePath, 'utf8').trim().split(/\r?\n/).filter(Boolean);
      const port = parseInt(lines[0], 10);
      if (port > 0 && port < 65536 && await checkPort(port)) {
        const wsPath = lines[1] || null;
        const version = await getChromeVersion(port);
        const browserName = filePath.includes('Chromium')
          ? 'Chromium'
          : filePath.includes('Chrome Canary')
            ? 'Google Chrome Canary'
            : 'Google Chrome';
        console.log(`[BossHunter Browser Runtime] DevToolsActivePort: ${port}${wsPath ? ' with wsPath' : ''}`);
        return { port, wsPath, product: version?.Browser || null, browserName };
      }
    } catch {}
  }

  for (const port of COMMON_PORTS) {
    if (await checkPort(port)) {
      const version = await getChromeVersion(port);
      if (version?.webSocketDebuggerUrl) {
        const product = version.Browser || null;
        const browserName = product?.startsWith('Edg/')
          ? 'Microsoft Edge'
          : product?.startsWith('Chrome/')
            ? 'Google Chrome'
            : product || '未知浏览器';
        console.log(`[BossHunter Browser Runtime] Found Chrome debug port: ${port}`);
        return { port, wsUrl: version.webSocketDebuggerUrl, product, browserName };
      }
    }
  }
  return null;
}

function getWebSocketUrl(port, wsPath, wsUrl) {
  if (wsUrl) return wsUrl;
  if (wsPath) return `ws://127.0.0.1:${port}${wsPath}`;
  return null;
}

async function connect() {
  if (ws && (ws.readyState === WS.OPEN || ws.readyState === 1)) return;
  if (connectingPromise) return connectingPromise;

  if (!chromePort) {
    const discovered = await discoverChromePort();
    if (!discovered) {
      throw new Error('Chrome debug port not found. Open Chrome remote debugging or start Chrome with --remote-debugging-port=9222.');
    }
    chromePort = discovered.port;
    chromeWsPath = discovered.wsPath || null;
    chromeWsUrl = discovered.wsUrl || null;
    chromeProduct = discovered.product || null;
    chromeName = discovered.browserName || null;
  }

  const wsUrl = getWebSocketUrl(chromePort, chromeWsPath, chromeWsUrl);
  if (!wsUrl) throw new Error('Chrome browser WebSocket URL not found. Check Chrome remote debugging permissions.');
  connectingPromise = new Promise((resolve, reject) => {
    ws = new WS(wsUrl);

    const onOpen = () => {
      cleanup();
      connectingPromise = null;
      console.log(`[BossHunter Browser Runtime] Connected to Chrome port ${chromePort}`);
      resolve();
    };
    const onError = (event) => {
      cleanup();
      connectingPromise = null;
      ws = null;
      chromePort = null;
      chromeWsPath = null;
      chromeWsUrl = null;
      chromeProduct = null;
      chromeName = null;
      const msg = event.message || event.error?.message || 'connection failed';
      console.error('[BossHunter Browser Runtime] Connection error:', msg);
      reject(new Error(msg));
    };
    const onClose = () => {
      console.log('[BossHunter Browser Runtime] Chrome connection closed');
      ws = null;
      chromePort = null;
      chromeWsPath = null;
      chromeWsUrl = null;
      chromeProduct = null;
      chromeName = null;
      sessions.clear();
      portGuardedSessions.clear();
    };
    const onMessage = (event) => {
      const data = typeof event === 'string' ? event : (event.data || event);
      const text = typeof data === 'string' ? data : data.toString();
      const msg = JSON.parse(text);

      if (msg.method === 'Target.attachedToTarget') {
        const { sessionId, targetInfo } = msg.params;
        sessions.set(targetInfo.targetId, sessionId);
      }
      if (msg.method === 'Fetch.requestPaused') {
        const { requestId, sessionId } = msg.params;
        sendCDP('Fetch.failRequest', { requestId, errorReason: 'ConnectionRefused' }, sessionId).catch(() => {});
      }
      if (msg.id && pending.has(msg.id)) {
        const { resolve, timer } = pending.get(msg.id);
        clearTimeout(timer);
        pending.delete(msg.id);
        resolve(msg);
      }
    };

    function cleanup() {
      ws.removeEventListener?.('open', onOpen);
      ws.removeEventListener?.('error', onError);
    }

    if (ws.on) {
      ws.on('open', onOpen);
      ws.on('error', onError);
      ws.on('close', onClose);
      ws.on('message', onMessage);
    } else {
      ws.addEventListener('open', onOpen);
      ws.addEventListener('error', onError);
      ws.addEventListener('close', onClose);
      ws.addEventListener('message', onMessage);
    }
  });

  return connectingPromise;
}

function sendCDP(method, params = {}, sessionId = null) {
  return new Promise((resolve, reject) => {
    if (!ws || (ws.readyState !== WS.OPEN && ws.readyState !== 1)) {
      reject(new Error('WebSocket is not connected'));
      return;
    }
    const id = ++cmdId;
    const msg = { id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP command timeout: ${method}`));
    }, 30000);
    pending.set(id, { resolve, timer });
    ws.send(JSON.stringify(msg));
  });
}

async function enablePortGuard(sessionId) {
  if (!ENABLE_PORT_GUARD || !chromePort || portGuardedSessions.has(sessionId)) return;
  try {
    await sendCDP('Fetch.enable', {
      patterns: [
        { urlPattern: `http://127.0.0.1:${chromePort}/*`, requestStage: 'Request' },
        { urlPattern: `http://localhost:${chromePort}/*`, requestStage: 'Request' },
      ],
    }, sessionId);
    portGuardedSessions.add(sessionId);
  } catch {}
}

async function ensureSession(targetId) {
  if (sessions.has(targetId)) return sessions.get(targetId);
  const resp = await sendCDP('Target.attachToTarget', { targetId, flatten: true });
  const sessionId = resp.result?.sessionId;
  if (!sessionId) throw new Error(`attach failed: ${JSON.stringify(resp.error)}`);
  sessions.set(targetId, sessionId);
  await enablePortGuard(sessionId);
  return sessionId;
}

async function waitForLoad(sessionId, timeoutMs = 15000) {
  await sendCDP('Page.enable', {}, sessionId);
  return new Promise((resolve) => {
    let resolved = false;
    const done = (result) => {
      if (resolved) return;
      resolved = true;
      clearTimeout(timer);
      clearInterval(checkInterval);
      resolve(result);
    };
    const timer = setTimeout(() => done('timeout'), timeoutMs);
    const checkInterval = setInterval(async () => {
      try {
        const resp = await sendCDP('Runtime.evaluate', {
          expression: 'document.readyState',
          returnByValue: true,
        }, sessionId);
        if (resp.result?.result?.value === 'complete') done('complete');
      } catch {}
    }, 500);
  });
}

async function readBody(req) {
  let body = '';
  for await (const chunk of req) body += chunk;
  return body;
}

function sendJson(res, data, statusCode = 200) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(data));
}

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const pathname = parsed.pathname;
  const q = Object.fromEntries(parsed.searchParams);

  try {
    if (pathname === '/health') {
      const connected = Boolean(ws && (ws.readyState === WS.OPEN || ws.readyState === 1));
      sendJson(res, { status: 'ok', runtime: RUNTIME_NAME, connected, sessions: sessions.size, chromePort, browserProduct: chromeProduct, browserName: chromeName, portGuard: ENABLE_PORT_GUARD });
      return;
    }

    await connect();

    if (pathname === '/targets') {
      const resp = await sendCDP('Target.getTargets');
      sendJson(res, resp.result.targetInfos.filter((target) => target.type === 'page'));
    } else if (pathname === '/new') {
      const targetUrl = q.url || 'about:blank';
      const background = q.background === '1' || q.background === 'true';
      const resp = await sendCDP('Target.createTarget', { url: targetUrl, background });
      const targetId = resp.result.targetId;
      if (targetUrl !== 'about:blank') {
        try {
          await ensureSession(targetId);
        } catch {}
      }
      sendJson(res, { targetId });
    } else if (pathname === '/close') {
      const resp = await sendCDP('Target.closeTarget', { targetId: q.target });
      sessions.delete(q.target);
      sendJson(res, resp.result || { ok: true });
    } else if (pathname === '/navigate') {
      const sessionId = await ensureSession(q.target);
      const resp = await sendCDP('Page.navigate', { url: q.url }, sessionId);
      await waitForLoad(sessionId);
      sendJson(res, resp.result || { ok: true });
    } else if (pathname === '/back') {
      const sessionId = await ensureSession(q.target);
      await sendCDP('Runtime.evaluate', { expression: 'history.back()' }, sessionId);
      await waitForLoad(sessionId);
      sendJson(res, { ok: true });
    } else if (pathname === '/eval') {
      const sessionId = await ensureSession(q.target);
      const expr = await readBody(req) || q.expr || 'document.title';
      const resp = await sendCDP('Runtime.evaluate', {
        expression: expr,
        returnByValue: true,
        awaitPromise: true,
      }, sessionId);
      if (resp.result?.exceptionDetails) {
        sendJson(res, { error: resp.result.exceptionDetails.text }, 400);
      } else if (resp.result?.result?.value !== undefined) {
        sendJson(res, { value: resp.result.result.value });
      } else {
        sendJson(res, resp.result || {});
      }
    } else if (pathname === '/click') {
      const sessionId = await ensureSession(q.target);
      const selector = await readBody(req);
      if (!selector) {
        sendJson(res, { error: 'POST body must be a CSS selector' }, 400);
        return;
      }
      const selectorJson = JSON.stringify(selector);
      const js = `(() => {
        const el = document.querySelector(${selectorJson});
        if (!el) return { error: 'Element not found: ' + ${selectorJson} };
        el.scrollIntoView({ block: 'center' });
        el.click();
        return { clicked: true, tag: el.tagName, text: (el.textContent || '').slice(0, 100) };
      })()`;
      const resp = await sendCDP('Runtime.evaluate', { expression: js, returnByValue: true, awaitPromise: true }, sessionId);
      const value = resp.result?.result?.value;
      sendJson(res, value || resp.result || {}, value?.error ? 400 : 200);
    } else if (pathname === '/clickAt') {
      const sessionId = await ensureSession(q.target);
      const body = (await readBody(req)).trim();
      if (!body) {
        sendJson(res, { error: 'POST body must be a CSS selector or x,y coordinates' }, 400);
        return;
      }
      let coord;
      const xyMatch = body.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
      if (xyMatch) {
        coord = { x: Number(xyMatch[1]), y: Number(xyMatch[2]), source: 'coordinates' };
      } else {
        const selectorJson = JSON.stringify(body);
        const js = `(async () => {
          const elements = Array.from(document.querySelectorAll(${selectorJson}));
          if (!elements.length) return { error: 'Element not found: ' + ${selectorJson} };
          const state = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const visible = !!(rect.width && rect.height && style.display !== 'none' && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
            const inViewport = visible && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
            const x = Math.min(Math.max(rect.x + rect.width / 2, 0), innerWidth - 1);
            const y = Math.min(Math.max(rect.y + rect.height / 2, 0), innerHeight - 1);
            const top = inViewport ? document.elementFromPoint(x, y) : null;
            return { el, rect, visible, inViewport, topmost: !!(top && (top === el || el.contains(top))) };
          };
          const candidates = elements.map(state).filter((item) => item.visible);
          if (!candidates.length) return { error: 'No visible element found: ' + ${selectorJson} };
          candidates.sort((a, b) => Number(b.topmost) - Number(a.topmost) || Number(b.inViewport) - Number(a.inViewport));
          const chosen = candidates[0];
          if (!chosen.inViewport) {
            chosen.el.scrollIntoView({ block: 'center', inline: 'center' });
            await new Promise((resolve) => setTimeout(resolve, 80));
          }
          const rect = chosen.el.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, tag: chosen.el.tagName, text: (chosen.el.textContent || '').slice(0, 100), source: 'selector' };
        })()`;
        const coordResp = await sendCDP('Runtime.evaluate', { expression: js, returnByValue: true, awaitPromise: true }, sessionId);
        coord = coordResp.result?.result?.value;
        if (!coord || coord.error) {
          sendJson(res, coord || coordResp.result || {}, 400);
          return;
        }
      }
      await sendCDP('Input.dispatchMouseEvent', { type: 'mouseMoved', x: coord.x, y: coord.y, button: 'none' }, sessionId);
      await new Promise((resolve) => setTimeout(resolve, 60));
      await sendCDP('Input.dispatchMouseEvent', { type: 'mousePressed', x: coord.x, y: coord.y, button: 'left', clickCount: 1 }, sessionId);
      await new Promise((resolve) => setTimeout(resolve, 80));
      await sendCDP('Input.dispatchMouseEvent', { type: 'mouseReleased', x: coord.x, y: coord.y, button: 'left', clickCount: 1 }, sessionId);
      sendJson(res, { clicked: true, x: coord.x, y: coord.y, tag: coord.tag, text: coord.text, source: coord.source });
    } else if (pathname === '/setFiles') {
      const sessionId = await ensureSession(q.target);
      const body = JSON.parse(await readBody(req));
      if (!body.selector || !body.files) {
        sendJson(res, { error: 'selector and files are required' }, 400);
        return;
      }
      await sendCDP('DOM.enable', {}, sessionId);
      const doc = await sendCDP('DOM.getDocument', {}, sessionId);
      const node = await sendCDP('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: body.selector }, sessionId);
      if (!node.result?.nodeId) {
        sendJson(res, { error: `Element not found: ${body.selector}` }, 400);
        return;
      }
      await sendCDP('DOM.setFileInputFiles', { nodeId: node.result.nodeId, files: body.files }, sessionId);
      sendJson(res, { success: true, files: body.files.length });
    } else if (pathname === '/type') {
      const sessionId = await ensureSession(q.target);
      const text = await readBody(req);
      if (!text) {
        sendJson(res, { error: 'POST body must contain text' }, 400);
        return;
      }
      if (q.human === '1') {
        for (const character of Array.from(text)) {
          await sendCDP('Input.insertText', { text: character }, sessionId);
          const punctuationPause = /[\u3002\uff0c\uff01\uff1f,.!?;:\n]/.test(character) ? 70 : 0;
          const delay = 25 + Math.floor(Math.random() * 46) + punctuationPause;
          await new Promise((resolve) => setTimeout(resolve, delay));
        }
      } else {
        await sendCDP('Input.insertText', { text }, sessionId);
      }
      sendJson(res, { typed: true, length: text.length, human: q.human === '1' });
    } else if (pathname === '/key') {
      const sessionId = await ensureSession(q.target);
      const key = (await readBody(req)).trim();
      if (key === 'SelectAll') {
        const onMac = process.platform === 'darwin';
        const modifierKey = onMac ? 'Meta' : 'Control';
        const modifierCode = onMac ? 'MetaLeft' : 'ControlLeft';
        const modifierVirtualKey = onMac ? 91 : 17;
        const modifiers = onMac ? 4 : 2;
        await sendCDP('Input.dispatchKeyEvent', {
          type: 'rawKeyDown', key: modifierKey, code: modifierCode,
          windowsVirtualKeyCode: modifierVirtualKey, nativeVirtualKeyCode: modifierVirtualKey,
          modifiers,
        }, sessionId);
        await sendCDP('Input.dispatchKeyEvent', {
          type: 'rawKeyDown', key: 'a', code: 'KeyA',
          windowsVirtualKeyCode: 65, nativeVirtualKeyCode: 65, modifiers,
        }, sessionId);
        await sendCDP('Input.dispatchKeyEvent', {
          type: 'keyUp', key: 'a', code: 'KeyA',
          windowsVirtualKeyCode: 65, nativeVirtualKeyCode: 65, modifiers,
        }, sessionId);
        await sendCDP('Input.dispatchKeyEvent', {
          type: 'keyUp', key: modifierKey, code: modifierCode,
          windowsVirtualKeyCode: modifierVirtualKey, nativeVirtualKeyCode: modifierVirtualKey,
        }, sessionId);
        sendJson(res, { pressed: true, key, platform: process.platform });
        return;
      }
      const supported = {
        Backspace: { code: 'Backspace', windowsVirtualKeyCode: 8, nativeVirtualKeyCode: 8 },
        Enter: { code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 },
      };
      const info = supported[key];
      if (!info) {
        sendJson(res, { error: 'Unsupported key' }, 400);
        return;
      }
      await sendCDP('Input.dispatchKeyEvent', { type: 'keyDown', key, ...info }, sessionId);
      await new Promise((resolve) => setTimeout(resolve, 50));
      await sendCDP('Input.dispatchKeyEvent', { type: 'keyUp', key, ...info }, sessionId);
      sendJson(res, { pressed: true, key });
    } else if (pathname === '/scroll') {
      const sessionId = await ensureSession(q.target);
      const y = parseInt(q.y || '3000', 10);
      const direction = q.direction || 'down';
      let js;
      if (direction === 'top') js = 'window.scrollTo(0, 0); "scrolled to top"';
      else if (direction === 'bottom') js = 'window.scrollTo(0, document.body.scrollHeight); "scrolled to bottom"';
      else if (direction === 'up') js = `window.scrollBy(0, -${Math.abs(y)}); "scrolled up ${Math.abs(y)}px"`;
      else js = `window.scrollBy(0, ${Math.abs(y)}); "scrolled down ${Math.abs(y)}px"`;
      const resp = await sendCDP('Runtime.evaluate', { expression: js, returnByValue: true }, sessionId);
      await new Promise((resolve) => setTimeout(resolve, 800));
      sendJson(res, { value: resp.result?.result?.value });
    } else if (pathname === '/screenshot') {
      const sessionId = await ensureSession(q.target);
      const format = q.format || 'png';
      const captureParams = { format, quality: format === 'jpeg' ? 80 : undefined };
      if (q.selector) {
        const expression = `(() => {
          const element = document.querySelector(${JSON.stringify(q.selector)});
          if (!element) return null;
          const rect = element.getBoundingClientRect();
          return { x: rect.left + window.scrollX, y: rect.top + window.scrollY, width: rect.width, height: rect.height };
        })()`;
        const geometry = await sendCDP('Runtime.evaluate', { expression, returnByValue: true }, sessionId);
        const clip = geometry.result?.result?.value;
        if (!clip || clip.width <= 0 || clip.height <= 0) {
          return sendJson(res, { error: 'Screenshot selector not found' }, 404);
        }
        captureParams.clip = { ...clip, scale: 1 };
        captureParams.captureBeyondViewport = true;
      }
      const resp = await sendCDP('Page.captureScreenshot', captureParams, sessionId);
      if (q.file) {
        fs.writeFileSync(q.file, Buffer.from(resp.result.data, 'base64'));
        sendJson(res, { saved: q.file });
      } else {
        res.setHeader('Content-Type', `image/${format}`);
        res.end(Buffer.from(resp.result.data, 'base64'));
      }
    } else if (pathname === '/pdf') {
      const sessionId = await ensureSession(q.target);
      const resp = await sendCDP('Page.printToPDF', {
        printBackground: true,
        preferCSSPageSize: true,
        marginTop: 0.4,
        marginBottom: 0.4,
        marginLeft: 0.4,
        marginRight: 0.4,
      }, sessionId);
      if (q.file) {
        fs.writeFileSync(q.file, Buffer.from(resp.result.data, 'base64'));
        sendJson(res, { saved: q.file });
      } else {
        res.setHeader('Content-Type', 'application/pdf');
        res.end(Buffer.from(resp.result.data, 'base64'));
      }
    } else if (pathname === '/info') {
      const sessionId = await ensureSession(q.target);
      const resp = await sendCDP('Runtime.evaluate', {
        expression: 'JSON.stringify({title: document.title, url: location.href, ready: document.readyState})',
        returnByValue: true,
      }, sessionId);
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(resp.result?.result?.value || '{}');
    } else {
      sendJson(res, {
        error: 'unknown endpoint',
        endpoints: ['/health', '/targets', '/new', '/close', '/navigate', '/back', '/eval', '/click', '/clickAt', '/type', '/key', '/setFiles', '/scroll', '/screenshot', '/pdf', '/info'],
      }, 404);
    }
  } catch (error) {
    sendJson(res, { error: error.message }, 500);
  }
});

function checkPortAvailable(port) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once('error', () => resolve(false));
    s.once('listening', () => { s.close(); resolve(true); });
    s.listen(port, '127.0.0.1');
  });
}

async function existingRuntimeHealthy(port) {
  try {
    return await new Promise((resolve) => {
      http.get(`http://127.0.0.1:${port}/health`, { timeout: 2000 }, (response) => {
        let data = '';
        response.on('data', (chunk) => data += chunk);
        response.on('end', () => {
          try {
            const health = JSON.parse(data);
            resolve(health.status === 'ok' && health.runtime === RUNTIME_NAME);
          } catch {
            resolve(false);
          }
        });
      }).on('error', () => resolve(false));
    });
  } catch {
    return false;
  }
}

async function main() {
  const available = await checkPortAvailable(PORT);
  if (!available) {
    if (await existingRuntimeHealthy(PORT)) {
      console.log(`[BossHunter Browser Runtime] Existing runtime is running on port ${PORT}`);
      process.exit(0);
    }
    console.error(`[BossHunter Browser Runtime] Port ${PORT} is occupied by another service`);
    process.exit(1);
  }

  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[BossHunter Browser Runtime] Listening on http://127.0.0.1:${PORT}`);
    connect().catch((error) => console.error('[BossHunter Browser Runtime] Initial Chrome connection failed:', error.message));
  });
}

process.on('uncaughtException', (error) => {
  console.error('[BossHunter Browser Runtime] Uncaught exception:', error.message);
});
process.on('unhandledRejection', (error) => {
  console.error('[BossHunter Browser Runtime] Unhandled rejection:', error?.message || error);
});

main();
