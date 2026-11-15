// TODO(T2.2): Implement Manifest V3 background service worker:
// - Listen to chrome.tabs.onActivated, onUpdated, onCreated, onRemoved, windows.onFocusChanged
// - Query all open tabs via chrome.tabs.query({}) and POST snapshot to http://127.0.0.1:8765/tabs
// - Poll http://127.0.0.1:8765/commands every ~1.5-2s (and via chrome.alarms fallback)
// - Execute chrome.tabs.remove(cmd.tab_ids) and POST result to http://127.0.0.1:8765/commands/ack

const SERVER_BASE = "http://127.0.0.1:8765";

console.log("Anti-Distractor extension stub loaded.");
