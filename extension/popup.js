const SERVER_BASE = "http://127.0.0.1:8765";

async function checkHealth() {
  const badge = document.getElementById("badge");
  const connStatus = document.getElementById("conn-status");
  const sessionStatus = document.getElementById("session-status");
  const goalStatus = document.getElementById("goal-status");

  try {
    const res = await fetch(`${SERVER_BASE}/health`);
    if (res.ok) {
      const data = await res.json();
      badge.className = "badge connected";
      connStatus.textContent = "Connected (127.0.0.1:8765)";
      sessionStatus.textContent = data.session_active ? "Active Monitoring" : "Idle / Setup";
      goalStatus.textContent = data.goal || "None";
      return;
    }
  } catch {
    // Server offline
  }

  badge.className = "badge disconnected";
  connStatus.textContent = "Offline (Python app not running)";
  sessionStatus.textContent = "Disconnected";
  goalStatus.textContent = "-";
}

document.addEventListener("DOMContentLoaded", checkHealth);
