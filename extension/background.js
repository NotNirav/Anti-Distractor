// Anti-Distractor Browser Sensor - Manifest V3 Background Service Worker
// Reports tab snapshots to http://127.0.0.1:8765/tabs and polls for close_tabs commands.

const SERVER_BASE = "http://127.0.0.1:8765";
const POLL_INTERVAL_MS = 1500;

function extractDomain(url) {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    let host = parsed.hostname.toLowerCase();
    if (host.startsWith("www.")) {
      host = host.slice(4);
    }
    return host;
  } catch {
    return "";
  }
}

let snapshotDebounceTimer = null;

function scheduleSnapshot(eventType, delayMs = 100) {
  if (snapshotDebounceTimer) {
    clearTimeout(snapshotDebounceTimer);
  }
  snapshotDebounceTimer = setTimeout(() => {
    sendSnapshot(eventType);
  }, delayMs);
}

async function sendSnapshot(eventType) {
  try {
    const [tabs, windows] = await Promise.all([
      chrome.tabs.query({}),
      chrome.windows.getAll({ populate: false })
    ]);

    const focusedWindow = windows.find(w => w.focused);
    const windowFocused = !!focusedWindow;
    const activeTab = tabs.find(t => t.active && (focusedWindow ? t.windowId === focusedWindow.id : true));
    const activeTabId = activeTab ? activeTab.id : (tabs.find(t => t.active)?.id ?? null);

    const tabPayloads = tabs.map(t => ({
      tab_id: t.id,
      window_id: t.windowId,
      title: t.title || "",
      url: t.url || "",
      domain: extractDomain(t.url),
      active: !!t.active,
      audible: !!t.audible
    }));

    await fetch(`${SERVER_BASE}/tabs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event_type: eventType,
        active_tab_id: activeTabId,
        window_focused: windowFocused,
        timestamp: Date.now() / 1000,
        tabs: tabPayloads
      })
    });
  } catch {
    // Fail silently when local server is not running
  }
}

async function executeCommand(cmd) {
  if (cmd.action === "close_tabs" && Array.isArray(cmd.tab_ids)) {
    const closed = [];
    const errors = [];
    for (const tid of cmd.tab_ids) {
      try {
        await chrome.tabs.remove(tid);
        closed.push(tid);
      } catch (err) {
        errors.push(String(err));
      }
    }

    try {
      await fetch(`${SERVER_BASE}/commands/ack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command_id: cmd.command_id,
          closed_tab_ids: closed,
          errors: errors
        })
      });
    } catch {
      // ignore
    }
  }
}

async function pollCommands() {
  try {
    const resp = await fetch(`${SERVER_BASE}/commands`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (data && Array.isArray(data.commands) && data.commands.length > 0) {
      for (const cmd of data.commands) {
        await executeCommand(cmd);
      }
    }
  } catch {
    // Fail silently when server is unreachable
  }
}

// Tab and window event listeners
chrome.tabs.onActivated.addListener(() => {
  scheduleSnapshot("tab_activated");
  pollCommands();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete" || changeInfo.title || changeInfo.url) {
    scheduleSnapshot("tab_updated");
    pollCommands();
  }
});

chrome.tabs.onCreated.addListener(() => {
  scheduleSnapshot("tab_created");
});

chrome.tabs.onRemoved.addListener(() => {
  scheduleSnapshot("tab_removed");
});

chrome.windows.onFocusChanged.addListener(windowId => {
  if (windowId !== chrome.windows.WINDOW_ID_NONE) {
    scheduleSnapshot("window_changed");
    pollCommands();
  }
});

// Periodic polling
setInterval(pollCommands, POLL_INTERVAL_MS);

// Alarms fallback for MV3 service worker keepalive
chrome.alarms.create("poll_keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "poll_keepalive") {
    pollCommands();
  }
});

// Lifecycle events
chrome.runtime.onInstalled.addListener(() => {
  sendSnapshot("baseline");
});

chrome.runtime.onStartup.addListener(() => {
  sendSnapshot("baseline");
});

console.log("Anti-Distractor browser sensor initialized.");
