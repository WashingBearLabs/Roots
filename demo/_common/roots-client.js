/* Roots Demo — API Client
 * Simple fetch wrapper for the Roots API with polling support.
 * No external dependencies — vanilla JS fetch API.
 */

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'paused', 'cancelled']);
const POLL_FAST_MS = 500;
const POLL_SLOW_MS = 3000;

class RootsClient {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
    this._pollTimer = null;
  }

  async createRun(processId, workItem) {
    const res = await fetch(`${this.baseUrl}/api/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ process_id: processId, work_item: workItem }),
    });
    return this._handleResponse(res);
  }

  async getRun(runId) {
    const res = await fetch(`${this.baseUrl}/api/runs/${encodeURIComponent(runId)}`);
    return this._handleResponse(res);
  }

  async getRunGraph(runId) {
    const res = await fetch(`${this.baseUrl}/api/runs/${encodeURIComponent(runId)}/graph`);
    return this._handleResponse(res);
  }

  async resolveCheckpoint(runId, decision, notes, redirectTo) {
    const body = { decision };
    if (notes != null) body.notes = notes;
    if (redirectTo != null) body.redirect_to = redirectTo;

    const res = await fetch(`${this.baseUrl}/api/runs/${encodeURIComponent(runId)}/checkpoint`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return this._handleResponse(res);
  }

  async listRuns() {
    const res = await fetch(`${this.baseUrl}/api/runs`);
    return this._handleResponse(res);
  }

  async getProcessGraph(processId) {
    const res = await fetch(`${this.baseUrl}/api/processes/${encodeURIComponent(processId)}/graph`);
    return this._handleResponse(res);
  }

  startPolling(runId, callback, intervalMs = POLL_FAST_MS) {
    this.stopPolling();

    let currentInterval = intervalMs;

    const poll = async () => {
      try {
        const data = await this.getRunGraph(runId);
        callback(data);

        if (data.run_status && TERMINAL_STATUSES.has(data.run_status)) {
          currentInterval = POLL_SLOW_MS;
        } else {
          currentInterval = intervalMs;
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
      this._pollTimer = setTimeout(poll, currentInterval);
    };

    poll();
  }

  stopPolling() {
    if (this._pollTimer != null) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
  }

  async _handleResponse(res) {
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`API error ${res.status}: ${text}`);
    }
    return res.json();
  }
}
