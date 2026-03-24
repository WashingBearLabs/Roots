/* Roots Demo — State Viewer
 * Renders JSON state as a collapsible tree with change highlighting.
 * No external dependencies — vanilla JS + DOM building.
 */

const TYPE_COLORS = {
  string: '#4ecca3',
  number: '#4a9ff5',
  boolean: '#f1c40f',
  null: '#555',
};

class StateViewer {
  constructor(container) {
    this.container = container;
  }

  render(state, previousState) {
    this.container.innerHTML = '';

    if (state == null) {
      const empty = document.createElement('div');
      empty.style.color = '#555';
      empty.style.padding = '1rem';
      empty.textContent = 'No state data';
      this.container.appendChild(empty);
      return;
    }

    const tree = this._buildTree(state, previousState, 0);
    this.container.appendChild(tree);
  }

  _buildTree(value, prev, depth) {
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      return this._buildObject(value, prev, depth);
    }
    if (Array.isArray(value)) {
      return this._buildArray(value, prev, depth);
    }
    return this._buildPrimitive(value);
  }

  _buildObject(obj, prev, depth) {
    const wrapper = document.createElement('div');

    const keys = Object.keys(obj);
    for (const key of keys) {
      const changed = prev != null
        && typeof prev === 'object'
        && !Array.isArray(prev)
        && JSON.stringify(prev[key]) !== JSON.stringify(obj[key]);

      const row = document.createElement('div');
      row.style.marginLeft = depth > 0 ? '1rem' : '0';
      if (changed) {
        row.style.borderLeft = '3px solid #f1c40f';
        row.style.paddingLeft = '0.5rem';
      }

      const childVal = obj[key];
      const isExpandable = childVal !== null && typeof childVal === 'object';

      if (isExpandable) {
        const details = document.createElement('details');
        if (depth === 0) details.open = true;

        const summary = document.createElement('summary');
        summary.style.cursor = 'pointer';
        summary.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
        summary.style.fontSize = '0.85rem';
        summary.style.padding = '0.15rem 0';
        summary.style.color = '#e0e0e0';
        summary.textContent = key;

        const childPrev = prev != null && typeof prev === 'object' ? prev[key] : undefined;
        const content = this._buildTree(childVal, childPrev, depth + 1);

        details.appendChild(summary);
        details.appendChild(content);
        row.appendChild(details);
      } else {
        const line = document.createElement('div');
        line.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
        line.style.fontSize = '0.85rem';
        line.style.padding = '0.15rem 0';

        const keySpan = document.createElement('span');
        keySpan.style.color = '#e0e0e0';
        keySpan.textContent = key + ': ';

        const valSpan = this._buildPrimitive(childVal);

        line.appendChild(keySpan);
        line.appendChild(valSpan);
        row.appendChild(line);
      }

      wrapper.appendChild(row);
    }

    return wrapper;
  }

  _buildArray(arr, prev, depth) {
    const wrapper = document.createElement('div');

    for (let i = 0; i < arr.length; i++) {
      const changed = Array.isArray(prev)
        && JSON.stringify(prev[i]) !== JSON.stringify(arr[i]);

      const row = document.createElement('div');
      row.style.marginLeft = '1rem';
      if (changed) {
        row.style.borderLeft = '3px solid #f1c40f';
        row.style.paddingLeft = '0.5rem';
      }

      const item = arr[i];
      const isExpandable = item !== null && typeof item === 'object';

      if (isExpandable) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.style.cursor = 'pointer';
        summary.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
        summary.style.fontSize = '0.85rem';
        summary.style.color = '#e0e0e0';
        summary.textContent = `[${i}]`;

        const prevItem = Array.isArray(prev) ? prev[i] : undefined;
        const content = this._buildTree(item, prevItem, depth + 1);

        details.appendChild(summary);
        details.appendChild(content);
        row.appendChild(details);
      } else {
        const line = document.createElement('div');
        line.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
        line.style.fontSize = '0.85rem';

        const idxSpan = document.createElement('span');
        idxSpan.style.color = '#e0e0e0';
        idxSpan.textContent = `[${i}]: `;

        const valSpan = this._buildPrimitive(item);

        line.appendChild(idxSpan);
        line.appendChild(valSpan);
        row.appendChild(line);
      }

      wrapper.appendChild(row);
    }

    return wrapper;
  }

  _buildPrimitive(value) {
    const span = document.createElement('span');
    span.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
    span.style.fontSize = '0.85rem';

    if (value === null || value === undefined) {
      span.style.color = TYPE_COLORS.null;
      span.textContent = 'null';
    } else if (typeof value === 'string') {
      span.style.color = TYPE_COLORS.string;
      span.textContent = `"${value}"`;
    } else if (typeof value === 'number') {
      span.style.color = TYPE_COLORS.number;
      span.textContent = String(value);
    } else if (typeof value === 'boolean') {
      span.style.color = TYPE_COLORS.boolean;
      span.textContent = String(value);
    } else {
      span.style.color = '#e0e0e0';
      span.textContent = String(value);
    }

    return span;
  }
}
