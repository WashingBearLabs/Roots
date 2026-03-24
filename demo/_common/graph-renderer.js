/* Roots Demo — Graph Renderer
 * Renders Roots graph JSON as SVG with status-colored nodes and edges.
 * No external dependencies — vanilla JS + inline SVG.
 */

const NODE_W = 160;
const NODE_H = 60;
const NODE_RX = 8;
const V_SPACING = 100;
const H_SPACING = 200;

const STATUS_COLORS = {
  pending: '#555',
  running: '#4a9ff5',
  completed: '#4ecca3',
  failed: '#e74c3c',
  paused: '#f1c40f',
  skipped: '#333',
};

class GraphRenderer {
  constructor(container) {
    this.container = container;
    this.svg = null;
    this._nodeElements = new Map();
    this._edgeElements = new Map();
  }

  render(graphData) {
    this.container.innerHTML = '';
    this._nodeElements.clear();
    this._edgeElements.clear();

    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];

    const positioned = this._applyLayout(nodes, edges);

    const bounds = this._calcBounds(positioned);
    const padX = 40;
    const padY = 40;
    const svgW = bounds.maxX + NODE_W + padX * 2;
    const svgH = bounds.maxY + NODE_H + padY * 2;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);
    svg.style.display = 'block';

    svg.appendChild(this._createDefs());

    const edgeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const nodeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');

    const nodeMap = new Map(positioned.map(n => [n.id, n]));

    for (const edge of edges) {
      const from = nodeMap.get(edge.from_node);
      const to = nodeMap.get(edge.to_node);
      if (!from || !to) continue;
      const el = this._createEdge(edge, from, to, padX, padY);
      edgeGroup.appendChild(el);
      this._edgeElements.set(edge.id, el);
    }

    for (const node of positioned) {
      const el = this._createNode(node, padX, padY);
      nodeGroup.appendChild(el);
      this._nodeElements.set(node.id, el);
    }

    svg.appendChild(edgeGroup);
    svg.appendChild(nodeGroup);
    this.container.appendChild(svg);
    this.svg = svg;
  }

  update(graphData) {
    if (!this.svg) {
      this.render(graphData);
      return;
    }

    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];

    for (const node of nodes) {
      const el = this._nodeElements.get(node.id);
      if (!el) continue;

      const rect = el.querySelector('rect');
      const color = STATUS_COLORS[node.status] || STATUS_COLORS.pending;
      rect.setAttribute('fill', color);

      if (node.status === 'running') {
        el.setAttribute('filter', 'url(#glow)');
        rect.classList.add('graph-pulse');
      } else {
        el.removeAttribute('filter');
        rect.classList.remove('graph-pulse');
      }

      const badge = el.querySelector('.status-text');
      if (badge) badge.textContent = node.status;
    }

    for (const edge of edges) {
      const el = this._edgeElements.get(edge.id);
      if (!el) continue;

      const traversed = edge.status === 'traversed';
      el.setAttribute('stroke', traversed ? '#4ecca3' : '#555');
      el.setAttribute('stroke-dasharray', traversed ? 'none' : '6 4');
    }
  }

  _applyLayout(nodes, edges) {
    const needsLayout = nodes.every(
      n => (!n.position || (n.position.x === 0 && n.position.y === 0))
    );

    if (!needsLayout) {
      return nodes.map(n => ({
        ...n,
        _x: n.position.x,
        _y: n.position.y,
      }));
    }

    // Build adjacency for topological sort
    const adj = new Map();
    const inDeg = new Map();
    for (const n of nodes) {
      adj.set(n.id, []);
      inDeg.set(n.id, 0);
    }
    for (const e of edges) {
      if (adj.has(e.from_node)) adj.get(e.from_node).push(e.to_node);
      inDeg.set(e.to_node, (inDeg.get(e.to_node) || 0) + 1);
    }

    // BFS layers
    const layers = [];
    let queue = nodes.filter(n => (inDeg.get(n.id) || 0) === 0).map(n => n.id);
    const visited = new Set();

    while (queue.length > 0) {
      layers.push([...queue]);
      queue.forEach(id => visited.add(id));
      const next = [];
      for (const id of queue) {
        for (const child of (adj.get(id) || [])) {
          if (!visited.has(child)) {
            inDeg.set(child, inDeg.get(child) - 1);
            if (inDeg.get(child) === 0) next.push(child);
          }
        }
      }
      queue = next;
    }

    // Place any unvisited nodes in a final layer
    const remaining = nodes.filter(n => !visited.has(n.id)).map(n => n.id);
    if (remaining.length) layers.push(remaining);

    const nodeById = new Map(nodes.map(n => [n.id, n]));
    const result = [];

    for (let row = 0; row < layers.length; row++) {
      const layer = layers[row];
      const totalW = layer.length * (NODE_W + H_SPACING) - H_SPACING;
      const startX = Math.max(0, (600 - totalW) / 2);

      for (let col = 0; col < layer.length; col++) {
        const node = nodeById.get(layer[col]);
        result.push({
          ...node,
          _x: startX + col * (NODE_W + H_SPACING),
          _y: row * (NODE_H + V_SPACING),
        });
      }
    }

    return result;
  }

  _calcBounds(positioned) {
    let maxX = 0;
    let maxY = 0;
    for (const n of positioned) {
      if (n._x > maxX) maxX = n._x;
      if (n._y > maxY) maxY = n._y;
    }
    return { maxX, maxY };
  }

  _createDefs() {
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

    // Glow filter for running nodes
    const filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
    filter.setAttribute('id', 'glow');
    filter.setAttribute('x', '-50%');
    filter.setAttribute('y', '-50%');
    filter.setAttribute('width', '200%');
    filter.setAttribute('height', '200%');

    const blur = document.createElementNS('http://www.w3.org/2000/svg', 'feGaussianBlur');
    blur.setAttribute('stdDeviation', '4');
    blur.setAttribute('result', 'coloredBlur');

    const merge = document.createElementNS('http://www.w3.org/2000/svg', 'feMerge');
    const mn1 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
    mn1.setAttribute('in', 'coloredBlur');
    const mn2 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
    mn2.setAttribute('in', 'SourceGraphic');
    merge.appendChild(mn1);
    merge.appendChild(mn2);
    filter.appendChild(blur);
    filter.appendChild(merge);

    // Pulse animation for running nodes
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    style.textContent = `
      .graph-pulse {
        animation: graphPulse 1.5s ease-in-out infinite;
      }
      @keyframes graphPulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
      }
    `;

    defs.appendChild(filter);
    defs.appendChild(style);
    return defs;
  }

  _createNode(node, padX, padY) {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const x = node._x + padX;
    const y = node._y + padY;
    const color = STATUS_COLORS[node.status] || STATUS_COLORS.pending;

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x);
    rect.setAttribute('y', y);
    rect.setAttribute('width', NODE_W);
    rect.setAttribute('height', NODE_H);
    rect.setAttribute('rx', NODE_RX);
    rect.setAttribute('fill', color);

    if (node.status === 'running') {
      g.setAttribute('filter', 'url(#glow)');
      rect.classList.add('graph-pulse');
    }

    // Label
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', x + NODE_W / 2);
    label.setAttribute('y', y + 20);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('fill', '#fff');
    label.setAttribute('font-size', '13');
    label.setAttribute('font-weight', '600');
    label.textContent = node.label || node.id;

    // Type subtitle
    const sub = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    sub.setAttribute('x', x + NODE_W / 2);
    sub.setAttribute('y', y + 35);
    sub.setAttribute('text-anchor', 'middle');
    sub.setAttribute('fill', 'rgba(255,255,255,0.7)');
    sub.setAttribute('font-size', '10');
    sub.textContent = node.type || '';

    // Status badge
    const badge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    badge.setAttribute('x', x + NODE_W / 2);
    badge.setAttribute('y', y + 50);
    badge.setAttribute('text-anchor', 'middle');
    badge.setAttribute('fill', 'rgba(255,255,255,0.9)');
    badge.setAttribute('font-size', '9');
    badge.setAttribute('class', 'status-text');
    badge.textContent = node.status;

    g.appendChild(rect);
    g.appendChild(label);
    g.appendChild(sub);
    g.appendChild(badge);
    return g;
  }

  _createEdge(edge, from, to, padX, padY) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', from._x + padX + NODE_W / 2);
    line.setAttribute('y1', from._y + padY + NODE_H);
    line.setAttribute('x2', to._x + padX + NODE_W / 2);
    line.setAttribute('y2', to._y + padY);

    const traversed = edge.status === 'traversed';
    line.setAttribute('stroke', traversed ? '#4ecca3' : '#555');
    line.setAttribute('stroke-width', '2');
    line.setAttribute('stroke-dasharray', traversed ? 'none' : '6 4');

    return line;
  }
}
