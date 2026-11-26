const SERVER_BASE = "http://127.0.0.1:8765";

let activeDomain = "";

function extractDomain(url) {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "";
    }
    let host = parsed.hostname.toLowerCase();
    if (host.startsWith("www.")) {
      host = host.slice(4);
    }
    return host;
  } catch {
    return "";
  }
}

function formatSeconds(secs) {
  const s = Math.max(0, Math.ceil(Number(secs) || 0));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}

async function resolveActiveTabDomain() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs.length > 0) {
      return extractDomain(tabs[0].url || "");
    }
  } catch {
    // Ignore
  }
  return "";
}

function renderRuleBadge(ruleEl, domainRule) {
  const rule = (domainRule || "neutral").toLowerCase();
  if (rule === "allowed") {
    ruleEl.textContent = "Allowed";
    ruleEl.className = "rule-tag allowed";
  } else if (rule === "borderline") {
    ruleEl.textContent = "Borderline";
    ruleEl.className = "rule-tag borderline";
  } else if (rule === "blocked") {
    ruleEl.textContent = "Blocked";
    ruleEl.className = "rule-tag blocked";
  } else {
    ruleEl.textContent = "AI-Judged";
    ruleEl.className = "rule-tag";
  }
}

async function pollStatus() {
  const badge = document.getElementById("badge");
  const statePill = document.getElementById("state-pill");
  const sessionStatus = document.getElementById("session-status");
  const timerStatus = document.getElementById("timer-status");
  const goalStatus = document.getElementById("goal-status");
  const tabDomainEl = document.getElementById("tab-domain");
  const tabRuleEl = document.getElementById("tab-rule");
  const btnAllow = document.getElementById("btn-allow");
  const btnBlock = document.getElementById("btn-block");

  activeDomain = await resolveActiveTabDomain();
  tabDomainEl.textContent = activeDomain || "Non-web tab";

  try {
    const query = activeDomain ? `?domain=${encodeURIComponent(activeDomain)}` : "";
    const res = await fetch(`${SERVER_BASE}/status${query}`);
    if (res.ok) {
      const data = await res.json();
      badge.className = "dot connected";

      if (!data.session_active) {
        statePill.textContent = "Idle";
        statePill.className = "pill";
        sessionStatus.textContent = "Ready (No active session)";
        timerStatus.textContent = "--:--";
        goalStatus.textContent = "None";
        tabRuleEl.textContent = "-";
        tabRuleEl.className = "rule-tag";
        btnAllow.disabled = true;
        btnBlock.disabled = true;
        return;
      }

      goalStatus.textContent = data.goal || "Focus Session";
      const modeTag = data.mode ? String(data.mode).toUpperCase() : "STRICT";

      if (data.paused) {
        statePill.textContent = "Break";
        statePill.className = "pill paused";
        sessionStatus.textContent = `Paused (${modeTag})`;
        timerStatus.textContent = formatSeconds(data.pause_remaining_seconds);
      } else if (
        data.state === "GRACE_PERIOD" ||
        data.state === "LAYER_1_ASK" ||
        data.state === "LAYER_2_ULTIMATUM"
      ) {
        statePill.textContent = "Off-Task";
        statePill.className = "pill alert";
        sessionStatus.textContent = `${data.state} (${modeTag})`;
        timerStatus.textContent = formatSeconds(data.remaining_seconds);
      } else {
        statePill.textContent = "Focusing";
        statePill.className = "pill active";
        sessionStatus.textContent = `Active (${modeTag})`;
        timerStatus.textContent = formatSeconds(data.remaining_seconds);
      }

      if (activeDomain) {
        renderRuleBadge(tabRuleEl, data.domain_rule);
        btnAllow.disabled = false;
        btnBlock.disabled = false;
      } else {
        tabRuleEl.textContent = "-";
        tabRuleEl.className = "rule-tag";
        btnAllow.disabled = true;
        btnBlock.disabled = true;
      }
      return;
    }
  } catch {
    // Offline fallback below
  }

  badge.className = "dot disconnected";
  statePill.textContent = "Offline";
  statePill.className = "pill";
  sessionStatus.textContent = "Python bridge not running";
  timerStatus.textContent = "--:--";
  goalStatus.textContent = "-";
  tabRuleEl.textContent = "-";
  tabRuleEl.className = "rule-tag";
  btnAllow.disabled = true;
  btnBlock.disabled = true;
}

async function sendQuickRule(action) {
  const feedback = document.getElementById("feedback");
  const tabRuleEl = document.getElementById("tab-rule");
  if (!activeDomain) return;

  try {
    const res = await fetch(`${SERVER_BASE}/rules/quick`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, domain: activeDomain })
    });
    if (res.ok) {
      const data = await res.json();
      renderRuleBadge(tabRuleEl, data.domain_rule);
      feedback.textContent =
        action === "allow"
          ? `Allowed ${data.domain} for this session.`
          : `Blocked ${data.domain} for this session.`;
    } else {
      feedback.textContent = "Could not update rule.";
    }
  } catch {
    feedback.textContent = "Bridge offline.";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btn-allow").addEventListener("click", () => sendQuickRule("allow"));
  document.getElementById("btn-block").addEventListener("click", () => sendQuickRule("block"));
  pollStatus();
  setInterval(pollStatus, 1000);
});
