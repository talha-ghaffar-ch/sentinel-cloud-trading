/**
 * terminal.js — Sentinel Cloud Trading
 * Real-time terminal state polling + command dispatch
 * Polls /api/terminal/state every 1 second and updates all UI elements.
 */

(function () {
  "use strict";

  const POLL_INTERVAL = 1000; // ms
  let lastUpdateTime  = 0;
  let pollTimer       = null;
  let consecutiveErrors = 0;

  // ── DOM refs ────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  // ── Utilities ───────────────────────────────────────────────
  function fmt(val, decimals = 2) {
    if (val === undefined || val === null) return "—";
    return Number(val).toFixed(decimals);
  }
  function fmtPct(val) { return fmt(val, 2) + "%"; }
  function fmtUSD(val) {
    const n = Number(val);
    const sign = n >= 0 ? "+" : "";
    return sign + "$" + fmt(Math.abs(n), 2);
  }
  function setClass(el, cls) {
    if (!el) return;
    el.className = el.className.replace(/\b(green|red|cyan|yellow)\b/g, "").trim();
    if (cls) el.classList.add(cls);
  }
  function colorForValue(val) {
    return Number(val) >= 0 ? "green" : "red";
  }

  // ── Connection indicator ─────────────────────────────────────
  function setConnectionStatus(status) {
    const dot   = $("conn-dot");
    const label = $("conn-label");
    if (!dot || !label) return;
    dot.className   = "conn-dot " + status;
    const labels    = { live: "LIVE", stale: "STALE", offline: "OFFLINE" };
    label.textContent = labels[status] || status;
  }

  // ── Main state renderer ──────────────────────────────────────
  function renderState(data) {
    if (!data || data.error) {
      setConnectionStatus("offline");
      return;
    }

    const sys   = data.system_status      || {};
    const perf  = data.performance_metrics|| {};
    const algo  = data.algo_scanner       || {};
    const ui    = data.ui_arrays          || {};
    const age   = Date.now() / 1000 - (data.last_updated || 0);

    setConnectionStatus(age < 5 ? "live" : age < 30 ? "stale" : "offline");

    // ── Header prices
    setEl("val-bid",    fmt(data.bid || 0, 5), "red");
    setEl("val-ask",    fmt(data.ask || 0, 5), "green");
    setEl("val-spread", fmt((((data.ask||0) - (data.bid||0)) * 10000), 1));

    // ── System status
    setEl("val-status",  sys.status_text || "—");
    setEl("val-mode",    sys.mode        || "NORMAL",
          sys.mode === "RECOVERY" ? "red" : "green");
    setEl("val-cooldown", sys.cooldown_remaining_sec > 0
          ? sys.cooldown_remaining_sec + "s" : "READY",
          sys.cooldown_remaining_sec > 0 ? "yellow" : "green");

    // ── Performance
    setEl("val-balance",  "$" + fmt(perf.live_balance, 2));
    setEl("val-equity",   "$" + fmt(perf.equity, 2));
    setEl("val-pnl",      fmtUSD(perf.session_pnl),   colorForValue(perf.session_pnl));
    setEl("val-open-pnl", fmtUSD(perf.open_pnl),      colorForValue(perf.open_pnl));
    setEl("val-trades",   perf.total_trades || 0);
    setEl("val-wins",     perf.wins || 0,    "green");
    setEl("val-wins-2",   perf.wins || 0,    "green");
    setEl("val-losses",   perf.losses || 0,  "red");
    setEl("val-losses-2", perf.losses || 0,  "red");
    const wr = perf.total_trades > 0
      ? ((perf.wins / perf.total_trades) * 100).toFixed(1) + "%" : "—";
    setEl("val-winrate",  wr, Number(perf.win_rate) >= 0.5 ? "green" : "red");

    // ── Drawdown bar
    const dd = Number(perf.drawdown_pct || 0);
    setEl("val-drawdown", fmtPct(dd), dd > 7 ? "red" : dd > 4 ? "yellow" : "green");
    const bar = $("drawdown-bar");
    if (bar) {
      bar.style.width = Math.min(dd, 100) + "%";
      bar.className   = "progress-bar-fill " + (dd > 7 ? "red" : dd > 4 ? "yellow" : "");
    }

    // ── AI / Algo scanner
    const sig  = algo.ai_signal || "WAIT";
    const sigEl= $("val-signal");
    if (sigEl) {
      sigEl.textContent = sig;
      sigEl.className   = "signal-badge " + sig;
    }
    setEl("val-confidence", fmtPct((algo.ai_confidence || 0) * 100),
          (algo.ai_confidence || 0) >= 0.65 ? "green" : "yellow");
    setEl("val-trend",   algo.trend_vector || "NEUTRAL",
          algo.trend_vector === "BULLISH" ? "green" : "red");
    setEl("val-macd",    fmt(algo.macd, 4));
    setEl("val-rsi",     fmt(algo.momentum_rsi, 1));
    setEl("val-position",algo.current_position_type || "NONE",
          algo.current_position_type === "LONG"  ? "green" :
          algo.current_position_type === "SHORT" ? "red"   : "");
    setEl("val-active-trades", perf.active_trades_count || 0);

    // ── Toggle buttons state
    updateToggleButtons(sys);

    // ── Logs
    renderLogs(ui.logs || []);

    // ── Trade history
    renderTradeHistory(ui.trade_history || []);
  }

  function setEl(id, value, colorClass) {
    const el = $(id);
    if (!el) return;
    el.textContent = value;
    if (colorClass !== undefined) setClass(el, colorClass);
  }

  function updateToggleButtons(sys) {
    const tradeBtn = $("btn-toggle-trade");
    const cbBtn    = $("btn-toggle-cb");

    if (tradeBtn) {
      if (sys.trading_enabled) {
        tradeBtn.textContent = "HALT TRADING";
        tradeBtn.className   = "btn btn-red btn-full";
      } else {
        tradeBtn.textContent = "ARM SYSTEM";
        tradeBtn.className   = "btn btn-green btn-full";
      }
    }

    if (cbBtn) {
      if (sys.circuit_breaker_enabled) {
        cbBtn.textContent  = "CB: ARMED";
        cbBtn.className    = "btn btn-outline btn-sm";
      } else {
        cbBtn.textContent  = "CB: DISABLED";
        cbBtn.className    = "btn btn-red btn-sm";
      }
    }

    const rebootSection = $("reboot-section");
    if (rebootSection) {
      rebootSection.style.display = sys.circuit_breaker_tripped ? "block" : "none";
    }
  }

  function renderLogs(logs) {
    const el = $("log-panel");
    if (!el) return;
    el.innerHTML = logs.map(l =>
      `<div class="log-line">${escapeHtml(l)}</div>`
    ).join("");
  }

  function renderTradeHistory(history) {
    const el = $("trade-history-panel");
    if (!el) return;
    if (!history.length) {
      el.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:24px;font-size:11px;">NO TRADES YET</div>';
      return;
    }
    el.innerHTML = history.map(t => {
      const pColor = Number(t.profit) >= 0 ? "green" : "red";
      const sign   = Number(t.profit) >= 0 ? "+" : "";
      return `<div class="metric-item">
        <span class="metric-key">${t.type} [${t.open}→${t.close}] ${t.dur}</span>
        <span class="metric-val ${pColor}">${sign}$${fmt(t.profit)}</span>
      </div>`;
    }).join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Polling ──────────────────────────────────────────────────
  async function poll() {
    try {
      const resp = await fetch("/api/terminal/state", { credentials: "same-origin" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      renderState(data);
      consecutiveErrors = 0;
    } catch (err) {
      consecutiveErrors++;
      console.warn("Poll error:", err);
      if (consecutiveErrors >= 3) setConnectionStatus("offline");
    } finally {
      pollTimer = setTimeout(poll, POLL_INTERVAL);
    }
  }

  // ── Command dispatch ─────────────────────────────────────────
  async function sendCommand(command) {
    const confirmMap = {
      "EMERGENCY_STOP": "⚠ EMERGENCY STOP will close ALL positions and shut down the engine. Confirm?",
      "CLOSE_ALL":      "Close ALL open positions now?",
    };
    if (confirmMap[command] && !confirm(confirmMap[command])) return;

    try {
      const resp = await fetch("/api/terminal/command", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ command }),
        credentials: "same-origin"
      });
      const data = await resp.json();
      if (!data.ok) {
        alert("Command failed: " + (data.error || "Unknown error"));
      } else {
        showToast(command + " sent ✓", "green");
      }
    } catch (err) {
      alert("Failed to send command: " + err.message);
    }
  }

  // ── Toast notifications ───────────────────────────────────────
  function showToast(message, type) {
    const toast = document.createElement("div");
    toast.style.cssText = `
      position:fixed; bottom:24px; right:24px; z-index:9999;
      background:var(--bg-card); border:1px solid var(--${type === "green" ? "green" : "red"});
      color:var(--${type === "green" ? "green" : "red"});
      padding:10px 20px; font-family:var(--font-mono); font-size:12px;
      animation: fadeIn 0.2s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
  }

  // ── Wire up buttons ───────────────────────────────────────────
  function wireButtons() {
    const bindings = [
      ["btn-toggle-trade",    "TOGGLE_TRADE"],
      ["btn-close-all",       "CLOSE_ALL"],
      ["btn-emergency-stop",  "EMERGENCY_STOP"],
      ["btn-toggle-cb",       "TOGGLE_CB"],
      ["btn-bypass-cooldown", "BYPASS"],
      ["btn-reboot-cb",       "REBOOT"],
    ];
    bindings.forEach(([id, cmd]) => {
      const el = $(id);
      if (el) el.addEventListener("click", () => sendCommand(cmd));
    });
  }

  // ── Init ─────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    wireButtons();
    poll(); // Start polling immediately
  });

})();
