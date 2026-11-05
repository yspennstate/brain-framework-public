"use strict";

const $ = (selector) => document.querySelector(selector);

const ui = {
  canvas: $("#graph-canvas"),
  empty: $("#canvas-empty"),
  tooltip: $("#tooltip"),
  layer: $("#layer-select"),
  session: $("#session-select"),
  search: $("#search-input"),
  fit: $("#fit-button"),
  pause: $("#pause-button"),
  visible: $("#visible-count"),
  modeNote: $("#mode-note"),
  caption: $("#frame-caption"),
  freshness: $("#freshness"),
  freshnessText: $("#freshness-text"),
  metricNodes: $("#metric-nodes"),
  metricEdges: $("#metric-edges"),
  metricFiles: $("#metric-files"),
  metricAgents: $("#metric-agents"),
  sourceList: $("#source-list"),
  warning: $("#warning-bar"),
  detailEmpty: $("#detail-empty"),
  detailContent: $("#detail-content"),
  detailType: $("#detail-type"),
  detailActivity: $("#detail-activity"),
  detailLabel: $("#detail-label"),
  detailMeaning: $("#detail-meaning"),
  detailFacts: $("#detail-facts"),
  detailDescription: $("#detail-description"),
  detailExcerptWrap: $("#detail-excerpt-wrap"),
  detailExcerpt: $("#detail-excerpt"),
  neighborsButton: $("#neighbors-button"),
  neighborWrap: $("#neighbor-wrap"),
  neighborList: $("#neighbor-list"),
  activityList: $("#activity-list"),
  activityClock: $("#activity-clock"),
  hotList: $("#hot-list"),
};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const compactNumber = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

function cssColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function formatAge(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "unknown";
  const value = Math.max(0, Number(seconds));
  if (value < 2) return "now";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  if (value < 86400) return `${Math.round(value / 3600)}h`;
  return `${Math.round(value / 86400)}d`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(Number(bytes))) return "";
  const value = Number(bytes);
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function hashString(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, r);
}

class GraphView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: true });
    this.nodes = [];
    this.edges = [];
    this.nodeMap = new Map();
    this.width = 0;
    this.height = 0;
    this.dpr = 1;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.selectedId = "";
    this.hoveredId = "";
    this.dragNode = null;
    this.dragStart = null;
    this.panStart = null;
    this.pointerId = null;
    this.motionPaused = reducedMotion;
    this.layoutFrames = 0;
    this.lastFrame = 0;
    this.lastDraw = 0;
    this.dataSignature = "";
    this.onSelect = () => {};
    this.colors = {};
    this.refreshColors();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement);
    this.resize();
    this.bindEvents();
    requestAnimationFrame((timestamp) => this.frame(timestamp));
  }

  refreshColors() {
    this.colors = {
      text: cssColor("--text"),
      soft: cssColor("--text-soft"),
      memory: cssColor("--memory"),
      concept: cssColor("--concept"),
      prompt: cssColor("--prompt"),
      tool: cssColor("--tool"),
      reflex: cssColor("--reflex"),
      agent: cssColor("--agent"),
      session: cssColor("--session"),
      fileHot: cssColor("--file-hot"),
      fileWarm: cssColor("--file-warm"),
      fileIdle: cssColor("--file-idle"),
      resource: cssColor("--resource"),
      edge: cssColor("--edge"),
      flow: cssColor("--flow"),
      bg: cssColor("--bg"),
    };
  }

  resize() {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    this.width = Math.max(1, rect.width);
    this.height = Math.max(1, rect.height);
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    if (!this.panX && !this.panY) {
      this.panX = this.width / 2;
      this.panY = this.height / 2;
    }
    this.layoutFrames = Math.max(this.layoutFrames, 20);
  }

  bindEvents() {
    this.canvas.addEventListener("pointerdown", (event) => this.pointerDown(event));
    this.canvas.addEventListener("pointermove", (event) => this.pointerMove(event));
    this.canvas.addEventListener("pointerup", (event) => this.pointerUp(event));
    this.canvas.addEventListener("pointercancel", (event) => this.pointerUp(event));
    this.canvas.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const sx = event.clientX - rect.left;
        const sy = event.clientY - rect.top;
        const before = this.screenToWorld(sx, sy);
        const factor = Math.exp(-event.deltaY * 0.0012);
        this.zoom = Math.min(3.6, Math.max(0.22, this.zoom * factor));
        this.panX = sx - before.x * this.zoom;
        this.panY = sy - before.y * this.zoom;
      },
      { passive: false },
    );
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      this.refreshColors();
    });
  }

  seedPosition(node, index, total) {
    const hash = hashString(node.id);
    const jitterX = ((hash & 255) / 255 - 0.5) * 90;
    const jitterY = (((hash >>> 8) & 255) / 255 - 0.5) * 90;
    const lane = {
      agent: { x: -this.width * 0.3, y: 0 },
      session: { x: -this.width * 0.23, y: this.height * 0.18 },
      file: { x: this.width * 0.31, y: 0 },
      folder: { x: this.width * 0.3, y: this.height * 0.2 },
      resource: { x: this.width * 0.22, y: this.height * 0.28 },
      reflex: { x: 0, y: -this.height * 0.25 },
      tool: { x: -this.width * 0.12, y: -this.height * 0.1 },
      prompt: { x: this.width * 0.08, y: this.height * 0.15 },
      concept: { x: 0, y: this.height * 0.12 },
      memory: { x: 0, y: 0 },
    }[node.type] || { x: 0, y: 0 };
    const angle = (index / Math.max(1, total)) * Math.PI * 2 + (hash % 100) / 100;
    const radius = node.direct || node.hot ? 55 : 115;
    return {
      x: lane.x + Math.cos(angle) * radius + jitterX,
      y: lane.y + Math.sin(angle) * radius + jitterY,
    };
  }

  radiusFor(node) {
    const base = {
      concept: 4.5,
      prompt: 7,
      tool: 7,
      memory: 8,
      reflex: 8,
      agent: 9,
      session: 7,
      file: 8,
      folder: 9,
      resource: 8,
    }[node.type] || 6;
    return base + Math.min(7, Math.sqrt(Math.max(0, node.activation || 0)) * 5);
  }

  setData(rawNodes, rawEdges) {
    const old = this.nodeMap;
    const signature = rawNodes
      .map((node) => `${node.id}:${Math.round((node.activation || 0) * 20)}:${node.hot ? 1 : 0}`)
      .sort()
      .join("|");
    this.nodes = rawNodes.map((raw, index) => {
      const previous = old.get(raw.id);
      const seeded = this.seedPosition(raw, index, rawNodes.length);
      return {
        ...raw,
        x: previous?.x ?? seeded.x,
        y: previous?.y ?? seeded.y,
        vx: previous?.vx ?? 0,
        vy: previous?.vy ?? 0,
        pinned: previous?.pinned ?? false,
        radius: this.radiusFor(raw),
      };
    });
    this.nodeMap = new Map(this.nodes.map((node) => [node.id, node]));
    this.edges = rawEdges
      .map((raw) => ({
        ...raw,
        sourceNode: this.nodeMap.get(raw.source),
        targetNode: this.nodeMap.get(raw.target),
      }))
      .filter((edge) => edge.sourceNode && edge.targetNode);
    const topologyChanged = signature !== this.dataSignature;
    this.dataSignature = signature;
    this.layoutFrames = topologyChanged ? 150 : Math.max(this.layoutFrames, 18);
  }

  setSelected(nodeId) {
    this.selectedId = nodeId || "";
  }

  setPaused(paused) {
    this.motionPaused = paused;
    if (!paused) this.layoutFrames = Math.max(this.layoutFrames, 40);
  }

  fit() {
    if (!this.nodes.length) return;
    const xs = this.nodes.map((node) => node.x);
    const ys = this.nodes.map((node) => node.y);
    const minX = Math.min(...xs) - 45;
    const maxX = Math.max(...xs) + 45;
    const minY = Math.min(...ys) - 45;
    const maxY = Math.max(...ys) + 45;
    const graphWidth = Math.max(100, maxX - minX);
    const graphHeight = Math.max(100, maxY - minY);
    this.zoom = Math.min(1.35, Math.max(0.28, Math.min(
      (this.width - 60) / graphWidth,
      (this.height - 70) / graphHeight,
    )));
    this.panX = this.width / 2 - ((minX + maxX) / 2) * this.zoom;
    this.panY = this.height / 2 - ((minY + maxY) / 2) * this.zoom;
  }

  centerNode(nodeId) {
    const node = this.nodeMap.get(nodeId);
    if (!node) return;
    this.panX = this.width / 2 - node.x * this.zoom;
    this.panY = this.height / 2 - node.y * this.zoom;
  }

  worldToScreen(x, y) {
    return { x: x * this.zoom + this.panX, y: y * this.zoom + this.panY };
  }

  screenToWorld(x, y) {
    return { x: (x - this.panX) / this.zoom, y: (y - this.panY) / this.zoom };
  }

  nodeAt(screenX, screenY) {
    const world = this.screenToWorld(screenX, screenY);
    for (let index = this.nodes.length - 1; index >= 0; index -= 1) {
      const node = this.nodes[index];
      const radius = node.radius + 5 / this.zoom;
      if ((node.x - world.x) ** 2 + (node.y - world.y) ** 2 <= radius ** 2) return node;
    }
    return null;
  }

  pointerDown(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const node = this.nodeAt(x, y);
    this.pointerId = event.pointerId;
    this.canvas.setPointerCapture(event.pointerId);
    this.dragStart = { x, y, clientX: event.clientX, clientY: event.clientY, moved: false };
    if (node) {
      this.dragNode = node;
      node.pinned = true;
    } else {
      this.panStart = { x: this.panX, y: this.panY, clientX: event.clientX, clientY: event.clientY };
    }
    this.canvas.classList.add("dragging");
  }

  pointerMove(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (this.pointerId === event.pointerId && this.dragStart) {
      if (Math.hypot(event.clientX - this.dragStart.clientX, event.clientY - this.dragStart.clientY) > 4) {
        this.dragStart.moved = true;
      }
      if (this.dragNode) {
        const world = this.screenToWorld(x, y);
        this.dragNode.x = world.x;
        this.dragNode.y = world.y;
        this.dragNode.vx = 0;
        this.dragNode.vy = 0;
        this.layoutFrames = Math.max(this.layoutFrames, 20);
      } else if (this.panStart) {
        this.panX = this.panStart.x + event.clientX - this.panStart.clientX;
        this.panY = this.panStart.y + event.clientY - this.panStart.clientY;
      }
      return;
    }
    const node = this.nodeAt(x, y);
    this.hoveredId = node?.id || "";
    if (node) {
      ui.tooltip.hidden = false;
      ui.tooltip.innerHTML = "";
      const label = document.createElement("strong");
      label.textContent = node.label;
      const type = document.createElement("span");
      type.className = "tooltip-type";
      type.textContent = `${node.type}${node.hot ? " · active now" : ""}`;
      ui.tooltip.append(label, type);
      const tipRect = ui.tooltip.getBoundingClientRect();
      ui.tooltip.style.left = `${Math.min(this.width - tipRect.width - 10, x + 14)}px`;
      ui.tooltip.style.top = `${Math.max(10, y - tipRect.height - 10)}px`;
    } else {
      ui.tooltip.hidden = true;
    }
  }

  pointerUp(event) {
    if (event.pointerId !== this.pointerId) return;
    if (this.dragStart && !this.dragStart.moved) {
      const rect = this.canvas.getBoundingClientRect();
      const node = this.nodeAt(event.clientX - rect.left, event.clientY - rect.top);
      if (node) this.onSelect(node.id);
    }
    this.dragNode = null;
    this.dragStart = null;
    this.panStart = null;
    this.pointerId = null;
    this.canvas.classList.remove("dragging");
    try {
      this.canvas.releasePointerCapture(event.pointerId);
    } catch {
      // The pointer may already have been released by the browser.
    }
  }

  typeTarget(node) {
    const targets = {
      agent: [-this.width * 0.26, 0],
      session: [-this.width * 0.2, this.height * 0.16],
      file: [this.width * 0.28, 0],
      folder: [this.width * 0.27, this.height * 0.18],
      resource: [this.width * 0.2, this.height * 0.26],
      reflex: [0, -this.height * 0.22],
      memory: [0, 0],
      concept: [0, this.height * 0.1],
      prompt: [this.width * 0.08, this.height * 0.15],
      tool: [-this.width * 0.08, -this.height * 0.1],
    };
    return targets[node.type] || [0, 0];
  }

  physicsStep() {
    if (this.motionPaused || this.layoutFrames <= 0) return;
    const nodes = this.nodes;
    for (const edge of this.edges) {
      const left = edge.sourceNode;
      const right = edge.targetNode;
      let dx = right.x - left.x;
      let dy = right.y - left.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      dx /= distance;
      dy /= distance;
      const direct = edge.direct ? 1 : 0;
      const desired = edge.relation.includes("claim") || edge.relation === "read" || edge.relation === "write"
        ? 105
        : 76 + (1 - Math.min(1, edge.flow || 0)) * 28;
      const force = (distance - desired) * (0.0045 + direct * 0.002);
      if (!left.pinned) {
        left.vx += dx * force;
        left.vy += dy * force;
      }
      if (!right.pinned) {
        right.vx -= dx * force;
        right.vy -= dy * force;
      }
    }

    for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
      const left = nodes[leftIndex];
      for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
        const right = nodes[rightIndex];
        const dx = right.x - left.x;
        const dy = right.y - left.y;
        const distanceSquared = dx * dx + dy * dy + 4;
        if (distanceSquared > 23000) continue;
        const distance = Math.sqrt(distanceSquared);
        const minimum = left.radius + right.radius + 15;
        const strength = distance < minimum
          ? 0.28 * (minimum - distance)
          : Math.min(0.11, 70 / distanceSquared);
        const fx = (dx / distance) * strength;
        const fy = (dy / distance) * strength;
        if (!left.pinned) {
          left.vx -= fx;
          left.vy -= fy;
        }
        if (!right.pinned) {
          right.vx += fx;
          right.vy += fy;
        }
      }
    }

    for (const node of nodes) {
      if (node.pinned) continue;
      const [targetX, targetY] = this.typeTarget(node);
      const pull = node.hot ? 0.0018 : 0.0012;
      node.vx += (targetX - node.x) * pull;
      node.vy += (targetY - node.y) * pull;
      node.vx *= 0.84;
      node.vy *= 0.84;
      const speed = Math.hypot(node.vx, node.vy);
      if (speed > 9) {
        node.vx = (node.vx / speed) * 9;
        node.vy = (node.vy / speed) * 9;
      }
      node.x += node.vx;
      node.y += node.vy;
    }
    this.layoutFrames -= 1;
  }

  colorFor(node) {
    if (node.type === "file") {
      if (node.hot) return this.colors.fileHot;
      if ((node.activation || 0) > 0.15) return this.colors.fileWarm;
      return this.colors.fileIdle;
    }
    return {
      memory: this.colors.memory,
      concept: this.colors.concept,
      prompt: this.colors.prompt,
      tool: this.colors.tool,
      reflex: this.colors.reflex,
      agent: this.colors.agent,
      session: this.colors.session,
      folder: this.colors.fileWarm,
      resource: this.colors.resource,
    }[node.type] || this.colors.resource;
  }

  drawNodeShape(ctx, node, radius) {
    const x = node.x;
    const y = node.y;
    ctx.beginPath();
    switch (node.type) {
      case "prompt":
        ctx.moveTo(x, y - radius);
        ctx.lineTo(x + radius, y);
        ctx.lineTo(x, y + radius);
        ctx.lineTo(x - radius, y);
        ctx.closePath();
        break;
      case "tool":
      case "file":
        roundedRect(ctx, x - radius * 1.15, y - radius * 0.78, radius * 2.3, radius * 1.56, 2.5);
        break;
      case "folder":
        roundedRect(ctx, x - radius * 1.15, y - radius * 0.72, radius * 2.3, radius * 1.55, 2.5);
        break;
      case "reflex":
        ctx.moveTo(x, y - radius * 1.12);
        ctx.lineTo(x + radius, y + radius * 0.8);
        ctx.lineTo(x - radius, y + radius * 0.8);
        ctx.closePath();
        break;
      case "agent":
      case "resource":
        for (let point = 0; point < 6; point += 1) {
          const angle = Math.PI / 3 * point - Math.PI / 2;
          const px = x + Math.cos(angle) * radius;
          const py = y + Math.sin(angle) * radius;
          if (point === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.closePath();
        break;
      case "session":
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        break;
      default:
        ctx.arc(x, y, radius, 0, Math.PI * 2);
    }
  }

  draw(timestamp) {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.save();
    ctx.translate(this.panX, this.panY);
    ctx.scale(this.zoom, this.zoom);

    for (const edge of this.edges) {
      const source = edge.sourceNode;
      const target = edge.targetNode;
      const flow = Math.max(0, Math.min(1, edge.flow || 0));
      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.strokeStyle = flow > 0.22
        ? `rgba(255, 179, 71, ${0.15 + flow * 0.28})`
        : this.colors.edge;
      ctx.lineWidth = (edge.direct ? 1.25 : 0.7) / Math.max(0.7, this.zoom);
      ctx.stroke();

      if (!this.motionPaused && !reducedMotion && flow > 0.12) {
        const speed = 0.00007 + flow * 0.00016;
        const phase = (timestamp * speed + (hashString(edge.id) % 1000) / 1000) % 1;
        const px = source.x + (target.x - source.x) * phase;
        const py = source.y + (target.y - source.y) * phase;
        ctx.beginPath();
        ctx.arc(px, py, (1.4 + flow * 1.7) / Math.sqrt(this.zoom), 0, Math.PI * 2);
        ctx.fillStyle = flow > 0.7 ? this.colors.fileHot : this.colors.flow;
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = 9 / this.zoom;
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    }

    const pulse = reducedMotion || this.motionPaused ? 0 : (Math.sin(timestamp * 0.004) + 1) / 2;
    const labelCandidates = [];
    for (const node of this.nodes) {
      const selected = node.id === this.selectedId;
      const hovered = node.id === this.hoveredId;
      const activation = Math.max(0, Math.min(1, node.activation || 0));
      const color = this.colorFor(node);
      const radius = node.radius + (node.hot ? pulse * 1.3 : 0);

      if (activation > 0.08 || selected || hovered || node.hot) {
        ctx.save();
        ctx.globalAlpha = 0.12 + activation * 0.18;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 7 + activation * 9 + pulse * 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      ctx.save();
      this.drawNodeShape(ctx, node, radius);
      ctx.fillStyle = color;
      ctx.globalAlpha = node.direct || node.hot ? 1 : 0.72 + activation * 0.25;
      ctx.shadowColor = color;
      ctx.shadowBlur = node.hot ? 20 / this.zoom : activation > 0.15 ? (7 + activation * 10) / this.zoom : 0;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
      ctx.lineWidth = (selected ? 2.2 : hovered ? 1.5 : 0.7) / Math.max(0.65, this.zoom);
      ctx.strokeStyle = selected ? this.colors.text : hovered ? this.colors.soft : "rgba(235,245,249,0.34)";
      ctx.stroke();
      if (node.type === "session") {
        ctx.beginPath();
        ctx.arc(node.x, node.y, Math.max(1, radius - 3), 0, Math.PI * 2);
        ctx.strokeStyle = this.colors.bg;
        ctx.lineWidth = 1.2 / Math.max(0.7, this.zoom);
        ctx.stroke();
      }
      ctx.restore();

      if (selected || hovered || node.hot || node.direct || (this.zoom > 1.1 && activation > 0.12)) {
        labelCandidates.push({ node, selected, hovered });
      }
    }

    labelCandidates
      .sort((left, right) => Number(right.selected) - Number(left.selected) || (right.node.activation || 0) - (left.node.activation || 0))
      .slice(0, 42)
      .forEach(({ node, selected }) => {
        const fontSize = (selected ? 12 : 10.5) / Math.max(0.72, Math.min(1.25, this.zoom));
        ctx.font = `${selected ? 600 : 500} ${fontSize}px ui-sans-serif, Segoe UI, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.lineWidth = 3.5 / this.zoom;
        ctx.strokeStyle = this.colors.bg;
        ctx.globalAlpha = 0.92;
        const text = node.label.length > 42 ? `${node.label.slice(0, 41)}…` : node.label;
        ctx.strokeText(text, node.x, node.y + node.radius + 5);
        ctx.fillStyle = selected ? this.colors.text : this.colors.soft;
        ctx.fillText(text, node.x, node.y + node.radius + 5);
        ctx.globalAlpha = 1;
      });
    ctx.restore();
  }

  frame(timestamp) {
    const elapsed = timestamp - this.lastFrame;
    if (elapsed >= 28) {
      this.physicsStep();
      this.draw(timestamp);
      this.lastFrame = timestamp;
    }
    requestAnimationFrame((next) => this.frame(next));
  }
}

const graph = new GraphView(ui.canvas);
let state = null;
let selectedNodeId = "";
let focusedNodeId = "";
let refreshTimer = null;
let currentRequest = null;
let searchTimer = null;
let connected = false;

function setConnection(kind, message) {
  ui.freshness.classList.toggle("stale", kind === "stale");
  ui.freshness.classList.toggle("offline", kind === "offline");
  ui.freshnessText.textContent = message;
}

function updateMetrics(metrics = {}) {
  ui.metricNodes.textContent = compactNumber.format(metrics.node_count || 0);
  ui.metricEdges.textContent = compactNumber.format(metrics.edge_count || 0);
  ui.metricFiles.textContent = compactNumber.format(metrics.active_files || 0);
  ui.metricAgents.textContent = compactNumber.format(metrics.live_agents || 0);
  ui.visible.textContent = `${metrics.visible_nodes || 0} shown · ${metrics.visible_edges || 0} visible links`;
}

function updateSources(sources = []) {
  ui.sourceList.replaceChildren();
  for (const source of sources) {
    const item = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `source-dot${source.ok ? "" : " bad"}`;
    const name = document.createElement("span");
    name.textContent = source.name;
    const age = document.createElement("span");
    age.className = "source-age";
    age.textContent = source.ok ? formatAge(source.age_s) : "missing";
    item.append(dot, name, age);
    ui.sourceList.append(item);
  }
}

function updateSessions(sessions = []) {
  const previous = ui.session.value || "all";
  const fragment = document.createDocumentFragment();
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = `All live sessions (${sessions.length})`;
  fragment.append(all);
  for (const session of sessions) {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = `${session.label} · ${session.node_count} active · ${formatAge(session.age_s)}`;
    fragment.append(option);
  }
  ui.session.replaceChildren(fragment);
  if ([...ui.session.options].some((option) => option.value === previous)) ui.session.value = previous;
}

function updateActivity(items = []) {
  ui.activityList.replaceChildren();
  for (const item of items.slice(0, 10)) {
    const row = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `feed-dot ${item.type === "claim" ? "claim" : ""}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.label;
    button.addEventListener("click", () => selectNode(item.node_id));
    row.append(dot, button);
    ui.activityList.append(row);
  }
  if (!items.length) {
    const row = document.createElement("li");
    row.textContent = "No file or claim pulse in this frame.";
    ui.activityList.append(row);
  }
  ui.activityClock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function nodeColorName(type, hot) {
  if (type === "file") return hot ? "--file-hot" : "--file-warm";
  return {
    memory: "--memory",
    concept: "--concept",
    prompt: "--prompt",
    tool: "--tool",
    reflex: "--reflex",
    agent: "--agent",
    session: "--session",
    folder: "--file-warm",
    resource: "--resource",
  }[type] || "--resource";
}

function updateHotNodes(nodes = []) {
  ui.hotList.replaceChildren();
  const hottest = [...nodes]
    .filter((node) => node.hot || node.direct || (node.activation || 0) > 0.08)
    .sort((left, right) => Number(right.hot) - Number(left.hot) || (right.activation || 0) - (left.activation || 0))
    .slice(0, 9);
  for (const node of hottest) {
    const button = document.createElement("button");
    button.type = "button";
    const dot = document.createElement("span");
    dot.className = "hot-node-dot";
    dot.style.background = cssColor(nodeColorName(node.type, node.hot));
    const label = document.createElement("span");
    label.textContent = node.label;
    const value = document.createElement("span");
    value.className = "hot-node-value";
    value.textContent = node.hot ? "LIVE" : `${Math.round((node.activation || 0) * 100)}%`;
    button.append(dot, label, value);
    button.addEventListener("click", () => selectNode(node.id));
    ui.hotList.append(button);
  }
}

function updateLayerText(layer) {
  const text = {
    live: {
      caption: "Current working set",
      note: "Measured activity + live diffusion over stored connections",
    },
    memory: {
      caption: "Durable memory network",
      note: "Memory-to-memory association map; glowing nodes are active now",
    },
    association: {
      caption: "Canonical association layer",
      note: "Prompts, concepts, tools, memories, co-activation and sequence",
    },
  }[layer];
  ui.caption.textContent = text.caption;
  ui.modeNote.textContent = text.note;
}

function applyState(next) {
  state = next;
  connected = true;
  updateMetrics(next.metrics);
  updateSources(next.sources);
  updateSessions(next.sessions);
  updateActivity(next.activity);
  updateHotNodes(next.nodes);
  updateLayerText(next.layer);
  ui.empty.hidden = next.nodes.length > 0;
  graph.setData(next.nodes, next.edges);
  graph.setSelected(selectedNodeId);

  const age = next.metrics?.freshest_age_s;
  if (next.stale) setConnection("stale", "Last good frame");
  else if (age !== null && age !== undefined && age > 180) setConnection("stale", `Live · source ${formatAge(age)} old`);
  else setConnection("live", `Live · refreshed ${formatAge(age || 0)} ago`);

  const warnings = next.warnings || [];
  ui.warning.hidden = warnings.length === 0;
  ui.warning.textContent = warnings.join(" · ");
}

async function refresh({ immediate = false } = {}) {
  clearTimeout(refreshTimer);
  if (currentRequest) currentRequest.abort();
  currentRequest = new AbortController();
  const params = new URLSearchParams({
    layer: ui.layer.value,
    session: ui.session.value || "all",
    q: ui.search.value.trim(),
    focus: focusedNodeId,
  });
  try {
    const response = await fetch(`/api/state?${params}`, {
      cache: "no-store",
      signal: currentRequest.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    applyState(await response.json());
  } catch (error) {
    if (error.name !== "AbortError") {
      setConnection("offline", connected ? "Refresh interrupted" : "Viewer offline");
    }
  } finally {
    currentRequest = null;
    const delay = document.hidden ? 9000 : 3000;
    refreshTimer = setTimeout(() => refresh(), immediate ? 50 : delay);
  }
}

function appendFact(fragment, label, value) {
  if (value === null || value === undefined || value === "" || value === false) return;
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = String(value);
  fragment.append(term, detail);
}

function renderNeighbors(neighbors = []) {
  ui.neighborList.replaceChildren();
  ui.neighborWrap.hidden = neighbors.length === 0;
  for (const neighbor of neighbors.slice(0, 12)) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const label = document.createElement("span");
    label.textContent = neighbor.label || neighbor.id;
    const weight = document.createElement("span");
    weight.className = "neighbor-weight";
    const numeric = Number(neighbor.weight);
    weight.textContent = Number.isFinite(numeric) ? numeric.toFixed(numeric >= 10 ? 0 : 2) : neighbor.relation || "";
    button.append(label, weight);
    button.addEventListener("click", () => selectNode(neighbor.id));
    item.append(button);
    ui.neighborList.append(item);
  }
}

function renderDetail(detail) {
  ui.detailEmpty.hidden = true;
  ui.detailContent.hidden = false;
  ui.detailType.textContent = detail.type || "node";
  ui.detailLabel.textContent = detail.label || detail.id;
  ui.detailMeaning.textContent = detail.meaning || "A node in the live brain.";
  ui.detailActivity.hidden = !(detail.hot || detail.activity === "claimed");
  ui.detailActivity.textContent = detail.activity === "claimed" ? "Claimed now" : "Active now";

  const facts = document.createDocumentFragment();
  appendFact(facts, "Activity", detail.activity || (detail.activation ? `${Math.round(detail.activation * 100)}%` : ""));
  appendFact(facts, "Operation", detail.operation);
  appendFact(facts, "Status", detail.status);
  appendFact(facts, "Runtime", detail.runtime);
  appendFact(facts, "Path", detail.path);
  appendFact(facts, "Claimed by", detail.claimed_by || detail.agent_id);
  appendFact(facts, "Purpose", detail.purpose);
  appendFact(facts, "Task", detail.task);
  appendFact(facts, "Source memory", detail.source_memory);
  appendFact(facts, "Validity", detail.validity);
  appendFact(facts, "Sessions", Array.isArray(detail.sessions) ? detail.sessions.map((item) => item.slice(0, 12)).join(", ") : detail.sessions);
  appendFact(facts, "Frequency", detail.frequency);
  appendFact(facts, "Specificity", detail.specificity);
  appendFact(facts, "Size", detail.size_bytes !== undefined ? formatBytes(detail.size_bytes) : "");
  appendFact(facts, "Last used", shortDate(detail.last_used));
  appendFact(facts, "Updated", shortDate(detail.updated));
  appendFact(facts, "Last seen", shortDate(detail.last_seen));
  appendFact(facts, "Expires", shortDate(detail.expires_at || detail.lease_until));
  ui.detailFacts.replaceChildren(facts);
  ui.detailFacts.hidden = ui.detailFacts.childElementCount === 0;

  const description = detail.description || detail.scar || "";
  ui.detailDescription.hidden = !description;
  ui.detailDescription.textContent = description;
  ui.detailExcerptWrap.hidden = !detail.excerpt;
  ui.detailExcerpt.textContent = detail.excerpt || "";
  renderNeighbors(detail.neighbors || []);
  ui.neighborsButton.hidden = !detail.id;
}

async function selectNode(nodeId) {
  if (!nodeId) return;
  selectedNodeId = nodeId;
  graph.setSelected(nodeId);
  graph.centerNode(nodeId);
  try {
    const response = await fetch(`/api/node?id=${encodeURIComponent(nodeId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDetail(await response.json());
  } catch {
    renderDetail({
      id: nodeId,
      type: "node",
      label: nodeId,
      meaning: "The detail source could not be read in this frame.",
    });
  }
}

graph.onSelect = selectNode;

ui.layer.addEventListener("change", () => {
  focusedNodeId = "";
  refresh({ immediate: true });
});

ui.session.addEventListener("change", () => refresh({ immediate: true }));

ui.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    focusedNodeId = "";
    refresh({ immediate: true });
  }, 280);
});

ui.fit.addEventListener("click", () => graph.fit());

ui.pause.addEventListener("click", () => {
  const paused = ui.pause.getAttribute("aria-pressed") !== "true";
  ui.pause.setAttribute("aria-pressed", String(paused));
  ui.pause.textContent = paused ? "Resume motion" : "Pause motion";
  graph.setPaused(paused);
});

if (reducedMotion) {
  ui.pause.setAttribute("aria-pressed", "true");
  ui.pause.textContent = "Resume motion";
}

ui.neighborsButton.addEventListener("click", () => {
  if (!selectedNodeId) return;
  focusedNodeId = selectedNodeId;
  refresh({ immediate: true });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== ui.search) {
    event.preventDefault();
    ui.search.focus();
  }
  if (event.key === "Escape") {
    selectedNodeId = "";
    focusedNodeId = "";
    graph.setSelected("");
    ui.detailContent.hidden = true;
    ui.detailEmpty.hidden = false;
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh({ immediate: true });
});

window.addEventListener("beforeunload", () => clearTimeout(refreshTimer));

refresh({ immediate: true });
