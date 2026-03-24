/* Roots Demo — Event Log
 * Scrolling event log with color-coded type badges.
 * No external dependencies — vanilla JS.
 */

const MAX_EVENTS = 100;

const BADGE_COLORS = {
  'run': '#4a9ff5',
  'node': '#4ecca3',
  'agent': '#00bcd4',
  'decision': '#9b59b6',
  'checkpoint': '#f1c40f',
  'escalation': '#e74c3c',
};

class EventLog {
  constructor(container) {
    this.container = container;
    this._count = 0;
  }

  addEvent(event) {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.alignItems = 'baseline';
    row.style.gap = '0.5rem';
    row.style.padding = '0.3rem 0';
    row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
    row.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
    row.style.fontSize = '0.8rem';
    row.style.animation = 'fadeIn 0.3s ease-out';

    // Timestamp
    const time = document.createElement('span');
    time.style.color = '#555';
    time.style.flexShrink = '0';
    time.textContent = this._formatTime(event.timestamp);

    // Type badge
    const badge = document.createElement('span');
    const typePrefix = (event.type || '').split('.')[0];
    const badgeColor = BADGE_COLORS[typePrefix] || '#555';
    badge.style.display = 'inline-block';
    badge.style.padding = '0.1rem 0.4rem';
    badge.style.borderRadius = '0.75rem';
    badge.style.fontSize = '0.7rem';
    badge.style.fontWeight = '600';
    badge.style.background = badgeColor;
    badge.style.color = typePrefix === 'checkpoint' ? '#1a1a2e' : '#fff';
    badge.style.flexShrink = '0';
    badge.textContent = event.type || 'unknown';

    // Node ID
    const nodeId = document.createElement('span');
    nodeId.style.color = '#888';
    nodeId.style.flexShrink = '0';
    nodeId.textContent = event.node_id || '';

    // Description
    const desc = document.createElement('span');
    desc.style.color = '#e0e0e0';
    desc.textContent = event.description || '';

    row.appendChild(time);
    row.appendChild(badge);
    row.appendChild(nodeId);
    row.appendChild(desc);

    // Prepend (newest at top)
    this.container.insertBefore(row, this.container.firstChild);
    this._count++;

    // Remove oldest events beyond MAX_EVENTS
    while (this._count > MAX_EVENTS && this.container.lastChild) {
      this.container.removeChild(this.container.lastChild);
      this._count--;
    }

    // Scroll to top
    this.container.scrollTop = 0;
  }

  _formatTime(timestamp) {
    if (!timestamp) {
      const now = new Date();
      return this._pad(now.getHours()) + ':' +
             this._pad(now.getMinutes()) + ':' +
             this._pad(now.getSeconds());
    }
    const d = new Date(timestamp);
    if (isNaN(d.getTime())) return timestamp;
    return this._pad(d.getHours()) + ':' +
           this._pad(d.getMinutes()) + ':' +
           this._pad(d.getSeconds());
  }

  _pad(n) {
    return String(n).padStart(2, '0');
  }
}
