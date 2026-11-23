/* TBox 图可视化：诊断推理弹窗内的预览图与独立图谱页共用同一套构图 / 布局 / 渲染逻辑。 */
(function () {
  'use strict';

  const KIND_META = {
    class:    { label: '类',       color: '#2456f5', fill: '#eef3ff', ink: '#1d3f9c' },
    relation: { label: '关系',     color: '#7c5cf0', fill: '#f1edff', ink: '#5540b4' },
    rule:     { label: '规则',     color: '#d97706', fill: '#fff4e2', ink: '#8a5200' },
    pricing:  { label: '定价参数', color: '#0f9d58', fill: '#e9f7ef', ink: '#0a6b3c' },
    action:   { label: '动作',     color: '#c9341c', fill: '#fdece8', ink: '#8d2613' },
    function: { label: '函数',     color: '#0891b2', fill: '#e5f6fa', ink: '#0b5f73' },
    group:    { label: '业务域',   color: '#64748b', fill: '#f0f3f7', ink: '#475569' },
    hub:      { label: '查询根',   color: '#8a7f70', fill: '#efeae2', ink: '#5b5347' },
    other:    { label: '其他',     color: '#6b7280', fill: '#f3f4f6', ink: '#374151' }
  };
  const FONT = '"Inter", "PingFang SC", "Microsoft YaHei", sans-serif';

  function kindMeta(kind) { return KIND_META[kind] || KIND_META.other; }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function charWidth(ch) {
    return /[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/.test(ch) ? 11.4 : 6.3;
  }

  function textWidth(text) {
    let width = 0;
    for (const ch of String(text || '')) width += charWidth(ch);
    return width;
  }

  function clip(text, max) {
    const chars = Array.from(String(text || ''));
    return chars.length > max ? chars.slice(0, max).join('') + '…' : chars.join('');
  }

  function round(value) { return Math.round(value * 10) / 10; }

  function indexOntology(onto) {
    const index = {
      classes: new Map(), relations: new Map(), rules: new Map(),
      actions: new Map(), functions: new Map(), pricing: (onto && onto.pricing) || {}
    };
    ((onto && onto.classes) || []).forEach((item) => item && item.id && index.classes.set(item.id, item));
    ((onto && onto.relations) || []).forEach((item) => item && item.id && index.relations.set(item.id, item));
    ((onto && onto.rules) || []).forEach((item) => item && item.id && index.rules.set(item.id, item));
    ((onto && onto.actions) || []).forEach((item) => item && item.id && index.actions.set(item.id, item));
    ((onto && onto.functions) || []).forEach((item) => item && item.id && index.functions.set(item.id, item));
    return index;
  }

  function classDetail(cls) {
    if (!cls) return '';
    const parts = [];
    if (cls.key) parts.push(`主键 ${cls.key}`);
    let props = cls.properties || [];
    if (!Array.isArray(props)) props = Object.keys(props);
    if (props.length) {
      parts.push(`属性 ${props.slice(0, 8).join(' / ')}${props.length > 8 ? ` 等 ${props.length} 项` : ''}`);
    }
    if (Array.isArray(cls.statuses) && cls.statuses.length) parts.push(`状态 ${cls.statuses.join(' / ')}`);
    if (cls.description) parts.push(cls.description);
    return parts.join('；');
  }

  function ruleDetail(rule) {
    if (!rule) return '';
    const judge = rule.judge || {};
    return `作用域 ${rule.domain || '—'}；${judge.explain || judge.formula || '规则定义'}`;
  }

  /**
   * 把 TBox items 映射成图。
   * - 关系 -> domain/range 之间的边
   * - 规则 -> 作用域类的边；定价参数 -> 绑定规则的边
   * - 动作 / 函数 -> 作用目标类的边
   * - 没有任何关联的孤立节点统一挂到“TBox 查询”根节点，避免散点不可读
   */
  function buildGraph(items, ontology, meta) {
    meta = meta || {};
    const list = Array.isArray(items) ? items.filter((item) => item && item.id) : [];
    const index = indexOntology(ontology);
    const nodes = new Map();
    const edges = [];
    const edgeKeys = new Set();

    function addNode(id, patch) {
      if (!id) return null;
      const key = String(id);
      const prev = nodes.get(key);
      if (prev) {
        if (patch.label) prev.label = patch.label;
        if (patch.detail) prev.detail = patch.detail;
        if (patch.kind) prev.kind = patch.kind;
        if (patch.ghost) prev.ghost = true;
        return prev;
      }
      const node = {
        id: key,
        kind: String(patch.kind || 'other').toLowerCase(),
        label: patch.label || key,
        detail: patch.detail || '',
        ghost: !!patch.ghost
      };
      nodes.set(key, node);
      return node;
    }

    function addEdge(from, to, label, kind) {
      if (!from || !to || from === to) return;
      const key = `${from}|${to}|${label || ''}`;
      if (edgeKeys.has(key)) return;
      edgeKeys.add(key);
      edges.push({ from, to, label: label || '', kind: kind || 'relation' });
    }

    function ensureClass(id) {
      if (!id || nodes.has(id)) return;
      const cls = index.classes.get(id);
      addNode(id, {
        kind: 'class',
        label: cls ? (cls.label || id) : id,
        detail: classDetail(cls),
        ghost: true
      });
    }

    function ensureRule(id) {
      if (!id) return;
      const rule = index.rules.get(id);
      if (!nodes.has(id)) {
        addNode(id, {
          kind: 'rule',
          label: rule ? (rule.name || id) : id,
          detail: ruleDetail(rule),
          ghost: true
        });
      }
      if (rule && rule.domain) {
        ensureClass(rule.domain);
        addEdge(id, rule.domain, '作用域', 'rule');
      }
    }

    list.forEach((item) => {
      addNode(item.id, {
        kind: String(item.kind || 'other').toLowerCase(),
        label: item.label || item.id,
        detail: item.detail || ''
      });
    });

    list.forEach((item) => {
      if (String(item.kind || '').toLowerCase() !== 'relation') return;
      const relation = index.relations.get(item.id);
      if (!relation || !relation.domain || !relation.range) return;
      ensureClass(relation.domain);
      ensureClass(relation.range);
      addEdge(relation.domain, relation.range, relation.label || item.label, 'relation');
    });

    list.forEach((item) => {
      const kind = String(item.kind || '').toLowerCase();
      if (kind === 'rule') ensureRule(item.id);
    });

    list.forEach((item) => {
      if (String(item.kind || '').toLowerCase() !== 'pricing') return;
      const fee = String(item.id || '').replace(/^pricing\./, '');
      const config = index.pricing[fee] || index.pricing[item.id] || {};
      const ruleId = config.rule_id;
      if (ruleId) {
        ensureRule(ruleId);
        addEdge(item.id, ruleId, '绑定规则', 'pricing');
      }
      // 决策表信息上节点：维度 + 行数 + 命中优先级说明（首行命中）
      const dims = Array.isArray(config.dimensions) ? config.dimensions : [];
      const rows = Array.isArray(config.matrix) ? config.matrix : [];
      const dimText = dims.length ? `维度: ${dims.join(' × ')}` : '';
      const rowText = rows.length
        ? `决策表 ${rows.length} 行（首行命中，具体行优先）`
        : '';
      const labelBits = [item.label || item.id];
      if (rows.length) labelBits.push(`${rows.length} 行`);
      const node = addNode(item.id, {
        kind: 'pricing',
        label: labelBits.join(' · '),
        detail: [dimText, rowText,
                 'matrix: 条件组合(车型/时段/天气) → 费率参数，默认行兜底']
          .filter(Boolean).join('；'),
      });
      if (node && rows.length) {
        node.matrix_summary = rows.map((row) => ({
          id: row.id || '',
          conditions: row.conditions || {},
          params: row.params || {},
        }));
      }
    });

    list.forEach((item) => {
      const kind = String(item.kind || '').toLowerCase();
      if (kind === 'action') {
        const action = index.actions.get(item.id);
        if (action && action.target) {
          ensureClass(action.target);
          addEdge(item.id, action.target, '作用于', 'action');
        }
      }
      if (kind === 'function') {
        const fn = index.functions.get(item.id)
          || index.functions.get(String(item.id).replace(/^function\./, ''));
        if (fn && fn.target) {
          ensureClass(fn.target);
          addEdge(item.id, fn.target, '作用于', 'function');
        }
      }
    });

    const degree = new Map();
    nodes.forEach((node) => degree.set(node.id, 0));
    edges.forEach((edge) => {
      degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
      degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
    });
    const orphans = Array.from(nodes.values()).filter((node) => !(degree.get(node.id) > 0));
    if (orphans.length) {
      addNode('__hub__', {
        kind: 'hub',
        label: 'TBox 查询',
        detail: meta.returned || `tbox.describe 返回 ${list.length} 项定义`
      });
      orphans.forEach((node) => addEdge('__hub__', node.id, '', 'hub'));
    }

    return {
      nodes: Array.from(nodes.values()),
      edges,
      sourceCount: list.length
    };
  }

  function nodeMetrics(node) {
    const maxChars = node.kind === 'hub' ? 10 : 15;
    const label = clip(node.label || node.id, maxChars);
    const width = Math.max(54, Math.min(180, 20 + textWidth(label)));
    return { width, height: node.kind === 'hub' ? 30 : 26, label };
  }

  function computeLayout(graph, width, height, iterations) {
    const nodes = graph.nodes;
    const positions = new Map();
    const total = nodes.length;
    if (!total) return positions;
    const pad = 58;
    const centerX = width / 2;
    const centerY = height / 2;
    const radiusX = Math.max(60, (width - pad * 2) / 2) * 0.82;
    const radiusY = Math.max(60, (height - pad * 2) / 2) * 0.82;

    nodes.forEach((node, i) => {
      if (node.kind === 'hub') {
        positions.set(node.id, { x: centerX, y: centerY });
        return;
      }
      const angle = (i / total) * Math.PI * 2 - Math.PI / 2;
      positions.set(node.id, {
        x: centerX + Math.cos(angle) * radiusX,
        y: centerY + Math.sin(angle) * radiusY
      });
    });

    const links = graph.edges
      .map((edge) => ({ a: edge.from, b: edge.to }))
      .filter((edge) => positions.has(edge.a) && positions.has(edge.b));
    const velocity = new Map(nodes.map((node) => [node.id, { x: 0, y: 0 }]));
    const repulse = 6400;
    const spring = 0.028;
    const desired = 118;
    const gravity = 0.012;
    const damping = 0.85;
    const cap = 16;

    for (let step = 0; step < (iterations || 240); step += 1) {
      const push = new Map(nodes.map((node) => [node.id, { x: 0, y: 0 }]));

      for (let i = 0; i < total; i += 1) {
        for (let j = i + 1; j < total; j += 1) {
          const a = nodes[i].id;
          const b = nodes[j].id;
          const pa = positions.get(a);
          const pb = positions.get(b);
          let dx = pa.x - pb.x;
          let dy = pa.y - pb.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 < 1) {
            dx = (i - j) || 1;
            dy = 1;
            dist2 = dx * dx + dy * dy;
          }
          const dist = Math.sqrt(dist2);
          const force = Math.min(repulse / dist2, 26);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          const sa = push.get(a);
          const sb = push.get(b);
          sa.x += fx; sa.y += fy;
          sb.x -= fx; sb.y -= fy;
        }
      }

      links.forEach((link) => {
        const pa = positions.get(link.a);
        const pb = positions.get(link.b);
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const dist = Math.hypot(dx, dy) || 1;
        const force = (dist - desired) * spring;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        const sa = push.get(link.a);
        const sb = push.get(link.b);
        sa.x += fx; sa.y += fy;
        sb.x -= fx; sb.y -= fy;
      });

      nodes.forEach((node) => {
        const pos = positions.get(node.id);
        const vel = velocity.get(node.id);
        const force = push.get(node.id);
        force.x += (centerX - pos.x) * gravity;
        force.y += (centerY - pos.y) * gravity;
        vel.x = (vel.x + force.x) * damping;
        vel.y = (vel.y + force.y) * damping;
        const speed = Math.hypot(vel.x, vel.y);
        if (speed > cap) {
          vel.x *= cap / speed;
          vel.y *= cap / speed;
        }
        pos.x = Math.max(pad, Math.min(width - pad, pos.x + vel.x));
        pos.y = Math.max(pad, Math.min(height - pad, pos.y + vel.y));
      });
    }
    return positions;
  }

  function boundaryPoint(cx, cy, width, height, dx, dy) {
    const scaleX = dx === 0 ? Infinity : (width / 2) / Math.abs(dx);
    const scaleY = dy === 0 ? Infinity : (height / 2) / Math.abs(dy);
    const scale = Math.min(scaleX, scaleY);
    return { x: cx + dx * scale, y: cy + dy * scale };
  }

  // 力导向只保证中心点距离，宽标签节点仍会互相压盖：按外接矩形做一轮硬分离
  function separateBoxes(positions, sizes, width, height) {
    const ids = Array.from(positions.keys());
    const pad = 8;
    const limit = 160;
    for (let round = 0; round < limit; round += 1) {
      let moved = false;
      for (let i = 0; i < ids.length; i += 1) {
        for (let j = i + 1; j < ids.length; j += 1) {
          const a = positions.get(ids[i]);
          const b = positions.get(ids[j]);
          const sa = sizes.get(ids[i]);
          const sb = sizes.get(ids[j]);
          if (!a || !b || !sa || !sb) continue;
          const halfW = (sa.width + sb.width) / 2 + pad;
          const halfH = (sa.height + sb.height) / 2 + pad;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const overlapX = halfW - Math.abs(dx);
          const overlapY = halfH - Math.abs(dy);
          if (overlapX <= 0 || overlapY <= 0) continue;
          moved = true;
          if (overlapX / halfW <= overlapY / halfH) {
            const shift = overlapX / 2 + 0.5;
            const sign = dx === 0 ? (i < j ? 1 : -1) : Math.sign(dx);
            a.x -= sign * shift;
            b.x += sign * shift;
          } else {
            const shift = overlapY / 2 + 0.5;
            const sign = dy === 0 ? (i < j ? 1 : -1) : Math.sign(dy);
            a.y -= sign * shift;
            b.y += sign * shift;
          }
        }
      }
      if (!moved) break;
    }
  }

  function buildScene(graph, width, height, iterations) {
    const positions = computeLayout(graph, width, height, iterations);
    const sizes = new Map();
    graph.nodes.forEach((node) => sizes.set(node.id, nodeMetrics(node)));
    separateBoxes(positions, sizes, width, height);

    const nodeList = graph.nodes.map((node) => {
      const pos = positions.get(node.id) || { x: width / 2, y: height / 2 };
      const size = sizes.get(node.id);
      return {
        node,
        x: round(pos.x - size.width / 2),
        y: round(pos.y - size.height / 2),
        cx: round(pos.x),
        cy: round(pos.y),
        width: size.width,
        height: size.height,
        label: size.label
      };
    });

    const edgeList = [];
    graph.edges.forEach((edge) => {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      const fromSize = sizes.get(edge.from);
      const toSize = sizes.get(edge.to);
      if (!from || !to || !fromSize || !toSize) return;
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const dist = Math.hypot(dx, dy) || 1;
      const ux = dx / dist;
      const uy = dy / dist;
      const start = boundaryPoint(from.x, from.y, fromSize.width, fromSize.height, ux, uy);
      const end = boundaryPoint(to.x, to.y, toSize.width, toSize.height, -ux, -uy);
      edgeList.push({
        edge,
        x1: round(start.x), y1: round(start.y),
        x2: round(end.x), y2: round(end.y),
        lx: round((start.x + end.x) / 2),
        ly: round((start.y + end.y) / 2)
      });
    });

    return { width, height, nodes: nodeList, edges: edgeList };
  }

  function edgeStroke(kind) {
    if (kind === 'hub') return { color: '#b8b0a3', dash: '4 4' };
    if (kind === 'pricing') return { color: '#a9cdb7', dash: '' };
    if (kind === 'rule') return { color: '#e0bd8a', dash: '' };
    if (kind === 'action') return { color: '#dfaea1', dash: '' };
    return { color: '#c3cddf', dash: '' };
  }

  function sceneMarkup(scene, opts) {
    opts = opts || {};
    const marker = opts.marker || 'tgArrow';
    const nodeFont = opts.nodeFont || 10.5;
    const edgeFont = opts.edgeFont || 9;
    const showEdgeLabels = opts.edgeLabels !== false;

    const edges = scene.edges.map((item) => {
      const stroke = edgeStroke(item.edge.kind);
      const dash = stroke.dash ? ` stroke-dasharray="${stroke.dash}"` : '';
      const label = showEdgeLabels && item.edge.label
        ? `<text x="${item.lx}" y="${item.ly - 4}" text-anchor="middle" font-size="${edgeFont}"
             fill="#7b879f" font-family='${FONT}' paint-order="stroke" stroke="#fff" stroke-width="3"
             stroke-linejoin="round">${esc(item.edge.label)}</text>`
        : '';
      return `<g class="tg-edge ${esc(item.edge.kind)}"
        data-edge-from="${esc(item.edge.from)}" data-edge-to="${esc(item.edge.to)}">
        <line x1="${item.x1}" y1="${item.y1}" x2="${item.x2}" y2="${item.y2}"
          stroke="${stroke.color}" stroke-width="1.1"${dash} marker-end="url(#${marker})"/>
        ${label}
      </g>`;
    }).join('');

    const nodes = scene.nodes.map((item) => {
      const meta = kindMeta(item.node.kind);
      const selected = opts.selectedId === item.node.id ? ' is-selected' : '';
      const dash = item.node.ghost ? ' stroke-dasharray="4 3"' : '';
      return `<g class="tg-node ${esc(item.node.kind)}${selected}" data-node-id="${esc(item.node.id)}">
        <title>${esc(item.node.label)}${item.node.detail ? ' · ' + esc(item.node.detail) : ''}</title>
        <rect x="${item.x}" y="${item.y}" width="${item.width}" height="${item.height}" rx="5"
          fill="${meta.fill}" stroke="${meta.color}" stroke-width="${item.node.ghost ? 1 : 1.3}"${dash}/>
        <text x="${item.cx}" y="${round(item.cy + nodeFont * 0.34)}" text-anchor="middle"
          font-size="${nodeFont}" font-family='${FONT}' fill="${meta.ink}">${esc(item.label)}</text>
      </g>`;
    }).join('');

    return { edges, nodes };
  }

  /* 弹窗预览画不下全部节点：按类型配额 + 度数挑出核心子图，避免挤成一团。 */
  function previewSubset(graph, maxNodes) {
    const limit = Math.max(10, maxNodes || 26);
    const visible = graph.nodes.filter((node) => node.kind !== 'hub');
    if (visible.length <= limit) {
      return {
        nodes: graph.nodes.slice(), edges: graph.edges.slice(),
        truncated: false, total: visible.length, shown: visible.length
      };
    }
    const degree = new Map();
    graph.edges.forEach((edge) => {
      if (edge.kind === 'hub') return;
      degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
      degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
    });
    const score = (node) => (degree.get(node.id) || 0) + (node.ghost ? 0 : 2);
    const byKind = {};
    visible.forEach((node) => {
      (byKind[node.kind] = byKind[node.kind] || []).push(node);
    });
    Object.keys(byKind).forEach((kind) => {
      byKind[kind].sort((a, b) => score(b) - score(a));
    });
    // 每种类型都给几个代表，保证预览图的图例与业务含义完整。
    const quota = { class: 8, relation: 8, rule: 3, pricing: 3, action: 2, function: 2 };
    const picked = new Map();
    Object.keys(quota).forEach((kind) => {
      (byKind[kind] || []).slice(0, quota[kind]).forEach((node) => picked.set(node.id, node));
    });
    visible
      .slice()
      .sort((a, b) => score(b) - score(a))
      .forEach((node) => {
        if (picked.size >= limit) return;
        if (!picked.has(node.id)) picked.set(node.id, node);
      });
    let chosen = Array.from(picked.values());
    if (chosen.length > limit) {
      chosen = chosen.sort((a, b) => score(b) - score(a)).slice(0, limit);
    }
    const ids = new Set(chosen.map((node) => node.id));
    return {
      nodes: visible.filter((node) => ids.has(node.id)),
      edges: graph.edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to)),
      truncated: true,
      total: visible.length,
      shown: ids.size,
      sourceCount: graph.sourceCount
    };
  }

  let markerSeq = 0;

  function renderPreview(graph, opts) {
    opts = opts || {};
    const width = opts.width || 660;
    const height = opts.height || 360;
    const subset = previewSubset(graph, opts.maxNodes || 26);
    const scene = buildScene(subset, width, height, 300);
    const marker = `tgArw${markerSeq += 1}`;
    const markup = sceneMarkup(scene, {
      marker,
      nodeFont: 10.5,
      edgeFont: 8.5,
      edgeLabels: subset.edges.length <= 18
    });
    const pad = 16;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    scene.nodes.forEach((item) => {
      minX = Math.min(minX, item.x);
      minY = Math.min(minY, item.y);
      maxX = Math.max(maxX, item.x + item.width);
      maxY = Math.max(maxY, item.y + item.height);
    });
    if (!Number.isFinite(minX)) { minX = 0; minY = 0; maxX = width; maxY = height; }
    const vbX = Math.round(minX - pad);
    const vbY = Math.round(minY - pad);
    let vbW = Math.round(Math.max(maxX - minX + pad * 2, 360));
    const vbH = Math.round(Math.max(maxY - minY + pad * 2, 220));
    // 保持与预览卡片一致的宽高比，避免内容变小后整块预览被拉得过高
    if (vbW / vbH < width / height) vbW = Math.round(vbH * width / height);
    const note = subset.truncated
      ? `<g class="tg-preview-note">
          <rect x="${vbX + vbW - 258}" y="${vbY + 10}" width="244" height="24" rx="12"
            fill="rgba(255,255,255,.92)" stroke="#e2e8f4"/>
          <text x="${vbX + vbW - 136}" y="${vbY + 26}" text-anchor="middle" font-size="10"
            font-family='${FONT}' fill="#6d7b99">仅显示核心 ${subset.shown} / ${subset.total} 个节点，点开查看全部</text>
        </g>`
      : '';
    return `<svg class="tg-preview" viewBox="${vbX} ${vbY} ${vbW} ${vbH}"
      preserveAspectRatio="xMidYMid meet" role="img" aria-label="TBox 本体图谱预览">
      <defs><marker id="${marker}" viewBox="0 0 8 8" refX="7" refY="4"
        markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0,0 L8,4 L0,8 z" fill="#aab6cc"/></marker></defs>
      <rect x="${vbX}" y="${vbY}" width="${vbW}" height="${vbH}" fill="#fbfcff"/>
      ${markup.edges}
      ${markup.nodes}
      ${note}
    </svg>`;
  }

  function countsByKind(graph) {
    const counts = {};
    graph.nodes.forEach((node) => {
      if (node.kind === 'hub') return;
      counts[node.kind] = (counts[node.kind] || 0) + 1;
    });
    return counts;
  }

  function countsText(graph) {
    const nodeCount = graph.nodes.filter((node) => node.kind !== 'hub').length;
    const edgeCount = graph.edges.filter((edge) => edge.kind !== 'hub').length;
    return `${nodeCount} 节点 · ${edgeCount} 条语义边`;
  }

  function legendHtml(graph) {
    const counts = countsByKind(graph);
    return Object.keys(KIND_META)
      .filter((kind) => counts[kind])
      .map((kind) => {
        const meta = KIND_META[kind];
        return `<button type="button" class="tg-legend-item" data-kind="${esc(kind)}"
          title="点击隐藏/显示该类节点"><i style="background:${meta.color}"></i>
          ${esc(meta.label)} <b>${counts[kind]}</b></button>`;
      }).join('');
  }

  function mountInteractive(svgEl, graph, opts) {
    opts = opts || {};
    const width = opts.width || 1260;
    const height = opts.height || 780;
    const scene = buildScene(graph, width, height, 420);
    const markup = sceneMarkup(scene, { marker: 'tgMainArrow', nodeFont: 12, edgeFont: 9.5 });
    const view = { x: 0, y: 0, k: 1 };
    const hiddenKinds = new Set();
    let isolatedId = null;

    const nodeMap = new Map();
    scene.nodes.forEach((item) => nodeMap.set(item.node.id, item));
    const dragBounds = scene.nodes.reduce((box, item) => ({
      minX: Math.min(box.minX, item.x),
      minY: Math.min(box.minY, item.y),
      maxX: Math.max(box.maxX, item.x + item.width),
      maxY: Math.max(box.maxY, item.y + item.height)
    }), { minX: 0, minY: 0, maxX: width, maxY: height });

    svgEl.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svgEl.innerHTML = `<defs><marker id="tgMainArrow" viewBox="0 0 8 8" refX="7" refY="4"
        markerWidth="6.5" markerHeight="6.5" orient="auto">
        <path d="M0,0 L8,4 L0,8 z" fill="#aab6cc"/></marker></defs>
      <g class="tg-viewport">${markup.edges}<g class="tg-nodes">${markup.nodes}</g></g>`;

    const viewport = svgEl.querySelector('.tg-viewport');
    const nodeEls = new Map();
    svgEl.querySelectorAll('[data-node-id]').forEach((group) => {
      nodeEls.set(group.getAttribute('data-node-id'), group);
    });
    const edgeEls = Array.from(svgEl.querySelectorAll('.tg-edge'));
    const applyView = () => {
      viewport.setAttribute('transform', `translate(${round(view.x)} ${round(view.y)}) scale(${round(view.k * 1000) / 1000})`);
    };

    function toUser(event) {
      const ctm = svgEl.getScreenCTM();
      if (!ctm) return { x: 0, y: 0 };
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
      return { x: point.x, y: point.y };
    }

    function toWorld(event) {
      const point = toUser(event);
      return {
        x: (point.x - view.x) / view.k,
        y: (point.y - view.y) / view.k
      };
    }

    function zoomAt(point, targetK) {
      const nextK = Math.max(0.3, Math.min(3.4, targetK));
      const gx = (point.x - view.x) / view.k;
      const gy = (point.y - view.y) / view.k;
      view.k = nextK;
      view.x = point.x - gx * nextK;
      view.y = point.y - gy * nextK;
      applyView();
    }

    function fit() {
      if (!scene.nodes.length) return;
      const pad = 56;
      const minX = Math.min.apply(null, scene.nodes.map((item) => item.x));
      const minY = Math.min.apply(null, scene.nodes.map((item) => item.y));
      const maxX = Math.max.apply(null, scene.nodes.map((item) => item.x + item.width));
      const maxY = Math.max.apply(null, scene.nodes.map((item) => item.y + item.height));
      const k = Math.max(0.3, Math.min(1.7,
        (width - pad * 2) / Math.max(1, maxX - minX),
        (height - pad * 2) / Math.max(1, maxY - minY)));
      view.k = k;
      view.x = (width - (minX + maxX) * k) / 2;
      view.y = (height - (minY + maxY) * k) / 2;
      applyView();
    }

    function select(nodeId) {
      svgEl.querySelectorAll('[data-node-id]').forEach((group) => {
        group.classList.toggle('is-selected', group.getAttribute('data-node-id') === nodeId);
      });
      if (typeof opts.onSelect === 'function') {
        const node = graph.nodes.find((item) => item.id === nodeId) || null;
        const related = node
          ? graph.edges.filter((edge) => edge.from === node.id || edge.to === node.id)
          : [];
        opts.onSelect(node, related);
      }
    }

    function redrawEdges(nodeId) {
      edgeEls.forEach((group) => {
        const fromId = group.getAttribute('data-edge-from');
        const toId = group.getAttribute('data-edge-to');
        if (nodeId && fromId !== nodeId && toId !== nodeId) return;
        const from = nodeMap.get(fromId);
        const to = nodeMap.get(toId);
        if (!from || !to) return;
        const dx = to.cx - from.cx;
        const dy = to.cy - from.cy;
        const dist = Math.hypot(dx, dy) || 1;
        const ux = dx / dist;
        const uy = dy / dist;
        const start = boundaryPoint(from.cx, from.cy, from.width, from.height, ux, uy);
        const end = boundaryPoint(to.cx, to.cy, to.width, to.height, -ux, -uy);
        const line = group.querySelector('line');
        if (line) {
          line.setAttribute('x1', round(start.x));
          line.setAttribute('y1', round(start.y));
          line.setAttribute('x2', round(end.x));
          line.setAttribute('y2', round(end.y));
        }
        const label = group.querySelector('text');
        if (label) {
          label.setAttribute('x', round((start.x + end.x) / 2));
          label.setAttribute('y', round((start.y + end.y) / 2 - 4));
        }
      });
    }

    function applyKindVisibility() {
      nodeEls.forEach((group, id) => {
        const item = nodeMap.get(id);
        group.classList.toggle('tg-hidden', !!item && hiddenKinds.has(item.node.kind));
      });
      edgeEls.forEach((group) => {
        const from = nodeMap.get(group.getAttribute('data-edge-from'));
        const to = nodeMap.get(group.getAttribute('data-edge-to'));
        const hidden = (from && hiddenKinds.has(from.node.kind))
          || (to && hiddenKinds.has(to.node.kind));
        group.classList.toggle('tg-hidden', !!hidden);
      });
    }

    function toggleKind(kind) {
      if (hiddenKinds.has(kind)) hiddenKinds.delete(kind);
      else hiddenKinds.add(kind);
      applyKindVisibility();
      return !hiddenKinds.has(kind);
    }

    function showAllKinds() {
      hiddenKinds.clear();
      applyKindVisibility();
    }

    function clearIsolation() {
      isolatedId = null;
      nodeEls.forEach((group) => group.classList.remove('tg-dim'));
      edgeEls.forEach((group) => group.classList.remove('tg-dim'));
    }

    function isolate(nodeId) {
      if (!nodeId || !nodeMap.has(nodeId)) return;
      isolatedId = nodeId;
      const keep = new Set([nodeId]);
      graph.edges.forEach((edge) => {
        if (edge.from === nodeId) keep.add(edge.to);
        if (edge.to === nodeId) keep.add(edge.from);
      });
      nodeEls.forEach((group, id) => group.classList.toggle('tg-dim', !keep.has(id)));
      edgeEls.forEach((group) => {
        const fromId = group.getAttribute('data-edge-from');
        const toId = group.getAttribute('data-edge-to');
        group.classList.toggle('tg-dim', fromId !== nodeId && toId !== nodeId);
      });
    }

    svgEl.addEventListener('wheel', (event) => {
      event.preventDefault();
      zoomAt(toUser(event), view.k * Math.exp(-event.deltaY * 0.0013));
    }, { passive: false });

    let panning = false;
    let nodeDrag = null;
    let suppressClick = false;
    let dragged = 0;
    let last = { x: 0, y: 0 };

    svgEl.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      const group = event.target.closest && event.target.closest('[data-node-id]');
      if (group) {
        const id = group.getAttribute('data-node-id');
        const item = nodeMap.get(id);
        if (!item) return;
        const point = toWorld(event);
        nodeDrag = {
          id, item, pointerId: event.pointerId,
          offsetX: point.x - item.x, offsetY: point.y - item.y,
          moved: 0
        };
        suppressClick = false;
        svgEl.setPointerCapture(event.pointerId);
        svgEl.style.cursor = 'grabbing';
        event.preventDefault();
        return;
      }
      panning = true;
      dragged = 0;
      last = toUser(event);
      svgEl.setPointerCapture(event.pointerId);
      svgEl.style.cursor = 'grabbing';
    });

    svgEl.addEventListener('pointermove', (event) => {
      if (nodeDrag) {
        const point = toWorld(event);
        const item = nodeDrag.item;
        const x = Math.max(dragBounds.minX, Math.min(dragBounds.maxX - item.width, point.x - nodeDrag.offsetX));
        const y = Math.max(dragBounds.minY, Math.min(dragBounds.maxY - item.height, point.y - nodeDrag.offsetY));
        nodeDrag.moved += Math.abs(x - item.x) + Math.abs(y - item.y);
        item.x = x;
        item.y = y;
        item.cx = x + item.width / 2;
        item.cy = y + item.height / 2;
        const group = nodeEls.get(nodeDrag.id);
        if (group) {
          const rect = group.querySelector('rect');
          const text = group.querySelector('text');
          if (rect) {
            rect.setAttribute('x', round(x));
            rect.setAttribute('y', round(y));
          }
          if (text) {
            text.setAttribute('x', round(item.cx));
            text.setAttribute('y', round(item.cy + 4.1));
          }
        }
        redrawEdges(nodeDrag.id);
        return;
      }
      if (!panning) return;
      const point = toUser(event);
      const dx = point.x - last.x;
      const dy = point.y - last.y;
      dragged += Math.abs(dx) + Math.abs(dy);
      view.x += dx / view.k;
      view.y += dy / view.k;
      last = point;
      applyView();
    });

    svgEl.addEventListener('pointerup', (event) => {
      if (nodeDrag) {
        const draggedNodeId = nodeDrag.id;
        const moved = nodeDrag.moved > 4;
        nodeDrag = null;
        suppressClick = true;
        window.setTimeout(() => { suppressClick = false; }, 0);
        svgEl.style.cursor = '';
        if (svgEl.hasPointerCapture(event.pointerId)) svgEl.releasePointerCapture(event.pointerId);
        if (!moved) select(draggedNodeId);
        return;
      }
      if (!panning) return;
      panning = false;
      svgEl.style.cursor = '';
      if (svgEl.hasPointerCapture(event.pointerId)) svgEl.releasePointerCapture(event.pointerId);
    });

    svgEl.addEventListener('click', (event) => {
      if (suppressClick) { suppressClick = false; return; }
      if (dragged > 4) { dragged = 0; return; }
      dragged = 0;
      const group = event.target.closest && event.target.closest('[data-node-id]');
      if (group) select(group.getAttribute('data-node-id'));
      else select(null);
    });

    fit();
    return {
      fit,
      select,
      zoomIn: () => zoomAt({ x: width / 2, y: height / 2 }, view.k * 1.25),
      zoomOut: () => zoomAt({ x: width / 2, y: height / 2 }, view.k / 1.25),
      toggleKind,
      showAllKinds,
      isolate,
      clearIsolation,
      getIsolated: () => isolatedId,
      scene
    };
  }

  window.TboxGraph = {
    KINDS: KIND_META,
    kindMeta,
    buildGraph,
    computeLayout,
    renderPreview,
    mountInteractive,
    countsText,
    legendHtml,
    countsByKind
  };
})();