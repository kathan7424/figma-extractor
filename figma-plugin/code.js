// Figma Export Kit - plugin main thread
// Exports: per-frame JSON, original images, SVG icons, raster fallbacks,
// frame renders, design tokens. Everything is sent to ui.html which zips it.

figma.showUI(__html__, { width: 380, height: 460, themeColors: true });

var VECTOR_TYPES = { VECTOR: 1, STAR: 1, LINE: 1, POLYGON: 1, BOOLEAN_OPERATION: 1 };
var SHAPE_TYPES = { VECTOR: 1, STAR: 1, LINE: 1, POLYGON: 1, BOOLEAN_OPERATION: 1, ELLIPSE: 1, RECTANGLE: 1 };
var CONTAINER_TYPES = { GROUP: 1, FRAME: 1, INSTANCE: 1, COMPONENT: 1 };

var BASE_KEYS = [
  'x', 'y', 'width', 'height', 'rotation', 'opacity', 'blendMode', 'isMask',
  'absoluteTransform', 'absoluteBoundingBox', 'absoluteRenderBounds',
  'fills', 'strokes', 'strokeWeight', 'strokeAlign', 'strokeCap', 'strokeJoin', 'dashPattern',
  'strokeTopWeight', 'strokeRightWeight', 'strokeBottomWeight', 'strokeLeftWeight',
  'cornerRadius', 'topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius', 'cornerSmoothing',
  'effects', 'clipsContent', 'constraints',
  'layoutMode', 'layoutWrap', 'primaryAxisAlignItems', 'counterAxisAlignItems', 'counterAxisAlignContent',
  'primaryAxisSizingMode', 'counterAxisSizingMode', 'itemSpacing', 'counterAxisSpacing',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'itemReverseZIndex', 'strokesIncludedInLayout',
  'layoutAlign', 'layoutGrow', 'layoutPositioning', 'layoutSizingHorizontal', 'layoutSizingVertical',
  'minWidth', 'maxWidth', 'minHeight', 'maxHeight', 'arcData', 'boundVariables'
];
var TEXT_KEYS = [
  'characters', 'textAlignHorizontal', 'textAlignVertical', 'textAutoResize', 'textTruncation',
  'maxLines', 'paragraphSpacing', 'paragraphIndent'
];
var SEGMENT_FIELDS = [
  'fontName', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'fills',
  'textDecoration', 'textCase', 'hyperlink'
];

var state;

function resetState() {
  state = {
    files: 0,
    imageHashes: {},
    images: {},
    svgByHash: {},
    fonts: {},
    usedPaths: {},
    cancelled: false,
    nodeCount: 0,
    warnings: []
  };
}

function send(path, data) {
  figma.ui.postMessage({ type: 'file', path: path, data: data });
  state.files++;
}

function progress(text) {
  figma.ui.postMessage({ type: 'progress', text: text });
}

function safeName(s) {
  var out = String(s || 'untitled').replace(/[\\/:*?"<>|#%{}^~\[\]`]+/g, '_').replace(/\s+/g, ' ').trim();
  if (!out) out = 'untitled';
  if (out.length > 60) out = out.slice(0, 60).trim();
  return out;
}

function uniquePath(dir, base, ext) {
  var p = dir + '/' + base + ext;
  var i = 2;
  while (state.usedPaths[p]) {
    p = dir + '/' + base + '-' + i + ext;
    i++;
  }
  state.usedPaths[p] = 1;
  return p;
}

function plain(v) {
  if (v === figma.mixed) return 'MIXED';
  if (v === undefined) return undefined;
  try { return JSON.parse(JSON.stringify(v)); } catch (e) { return undefined; }
}

function pick(node, keys, o) {
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    if (!(k in node)) continue;
    try {
      var v = plain(node[k]);
      if (v !== undefined && v !== null) o[k] = v;
    } catch (e) { /* some props throw for some node types */ }
  }
}

function fnv(bytes) {
  var h = 0x811c9dc5;
  for (var i = 0; i < bytes.length; i++) {
    h ^= bytes[i];
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return ('00000000' + h.toString(16)).slice(-8);
}

function imageExt(bytes) {
  if (bytes[0] === 0x89 && bytes[1] === 0x50) return '.png';
  if (bytes[0] === 0xff && bytes[1] === 0xd8) return '.jpg';
  if (bytes[0] === 0x47 && bytes[1] === 0x49) return '.gif';
  if (bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[8] === 0x57) return '.webp';
  return '.png';
}

function visibleChildren(node) {
  if (!('children' in node)) return [];
  return node.children.filter(function (c) { return c.visible; });
}

function hasImageFill(node) {
  if (!('fills' in node) || node.fills === figma.mixed) return false;
  return node.fills.some(function (p) { return p.visible !== false && p.type === 'IMAGE'; });
}

function collectImageHashes(paints) {
  if (!paints || paints === figma.mixed) return;
  for (var i = 0; i < paints.length; i++) {
    var p = paints[i];
    if (p.type === 'IMAGE' && p.imageHash) state.imageHashes[p.imageHash] = 1;
  }
}

// A group/frame made only of vector shapes (an icon or illustration) -> one SVG.
function isIconGroup(node) {
  if (!CONTAINER_TYPES[node.type]) return false;
  if (hasImageFill(node)) return false;
  var hasVector = false;
  function walk(n) {
    var kids = visibleChildren(n);
    if (kids.length === 0) return CONTAINER_TYPES[n.type] ? false : true;
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c.type === 'TEXT') return false;
      if (hasImageFill(c)) return false;
      if (VECTOR_TYPES[c.type]) { hasVector = true; continue; }
      if (SHAPE_TYPES[c.type]) continue;
      if (CONTAINER_TYPES[c.type]) { if (!walk(c)) return false; continue; }
      return false;
    }
    return true;
  }
  var ok = walk(node);
  return ok && hasVector;
}

function hasMaskChild(node) {
  return visibleChildren(node).some(function (c) { return c.isMask; });
}

function arcIsPartial(node) {
  if (node.type !== 'ELLIPSE' || !node.arcData) return false;
  var a = node.arcData;
  var sweep = Math.abs(a.endingAngle - a.startingAngle);
  return a.innerRadius > 0 || sweep < Math.PI * 2 - 0.001;
}

// Things CSS cannot reproduce exactly: let Figma render them (PNG @2x).
function needsRaster(node) {
  try {
    if ('effects' in node) {
      for (var i = 0; i < node.effects.length; i++) {
        var e = node.effects[i];
        if (e.visible === false) continue;
        if (e.type === 'NOISE' || e.type === 'TEXTURE' || e.type === 'GLASS') return true;
        if ((e.type === 'LAYER_BLUR' || e.type === 'BACKGROUND_BLUR') && e.blurType === 'PROGRESSIVE') return true;
      }
    }
    if ('fills' in node && node.fills !== figma.mixed) {
      for (var j = 0; j < node.fills.length; j++) {
        var p = node.fills[j];
        if (p.visible === false) continue;
        if (p.type === 'GRADIENT_DIAMOND' || p.type === 'VIDEO' || p.type === 'PATTERN') return true;
        if (p.type === 'IMAGE' && p.filters) {
          for (var k in p.filters) { if (Math.abs(p.filters[k]) > 0.001) return true; }
        }
      }
    }
  } catch (err) { /* ignore */ }
  return false;
}

function decideAsset(node, depth) {
  if (depth === 0) return null;
  if (node.type !== 'TEXT' && needsRaster(node)) return 'png';
  if (VECTOR_TYPES[node.type]) return 'svg';
  if (arcIsPartial(node)) return 'svg';
  if ('children' in node && hasMaskChild(node)) return 'png';
  if (isIconGroup(node)) return 'svg';
  return null;
}

async function exportAsset(node, kind) {
  var bytes;
  try {
    if (kind === 'svg') {
      bytes = await node.exportAsync({ format: 'SVG', svgOutlineText: true, svgIdAttribute: false, svgSimplifyStroke: true });
    }
  } catch (e) { bytes = null; }
  if (!bytes) {
    kind = 'png';
    try {
      bytes = await node.exportAsync({ format: 'PNG', constraint: { type: 'SCALE', value: 2 } });
    } catch (e2) {
      state.warnings.push('Could not export "' + node.name + '": ' + e2);
      return null;
    }
  }
  var hash = fnv(bytes);
  var key = kind + ':' + hash;
  var path = state.svgByHash[key];
  if (!path) {
    var dir = kind === 'svg' ? 'assets/svg' : 'assets/raster';
    path = uniquePath(dir, safeName(node.name).replace(/ /g, '-').toLowerCase() + '-' + hash, kind === 'svg' ? '.svg' : '.png');
    state.svgByHash[key] = path;
    send(path, bytes);
  }
  return { path: path, format: kind, scale: kind === 'png' ? 2 : 1, renderBounds: plain(node.absoluteRenderBounds) || plain(node.absoluteBoundingBox) };
}

async function serialize(node, depth) {
  if (!node.visible) return null;
  if (state.cancelled) throw new Error('Cancelled');
  state.nodeCount++;
  if (state.nodeCount % 200 === 0) progress('Reading layers... ' + state.nodeCount);

  var o = { id: node.id, name: node.name, type: node.type };
  pick(node, BASE_KEYS, o);
  if ('fills' in node) collectImageHashes(node.fills);
  if ('strokes' in node) collectImageHashes(node.strokes);

  if (node.type === 'INSTANCE') {
    try {
      var mc = await node.getMainComponentAsync();
      if (mc) o.componentName = mc.parent && mc.parent.type === 'COMPONENT_SET' ? mc.parent.name + ' / ' + mc.name : mc.name;
    } catch (e) { /* ignore */ }
  }

  var assetKind = decideAsset(node, depth);
  if (assetKind) {
    var asset = await exportAsset(node, assetKind);
    if (asset) { o.asset = asset; return o; }
  }

  if (node.type === 'TEXT') {
    pick(node, TEXT_KEYS, o);
    try {
      var segs = node.getStyledTextSegments(SEGMENT_FIELDS);
      o.segments = segs.map(function (s) {
        var seg = {};
        for (var i = 0; i < SEGMENT_FIELDS.length; i++) {
          var f = SEGMENT_FIELDS[i];
          if (s[f] !== undefined) seg[f] = plain(s[f]);
        }
        seg.characters = s.characters;
        seg.start = s.start;
        seg.end = s.end;
        if (s.fontName) {
          state.fonts[s.fontName.family + '|' + s.fontName.style] = { family: s.fontName.family, style: s.fontName.style, weight: s.fontWeight };
        }
        collectImageHashes(s.fills);
        return seg;
      });
    } catch (e) {
      state.warnings.push('Text segments failed for "' + node.name + '": ' + e);
    }
    return o;
  }

  if ('children' in node) {
    o.children = [];
    for (var i = 0; i < node.children.length; i++) {
      var c = await serialize(node.children[i], depth + 1);
      if (c) o.children.push(c);
    }
  }
  return o;
}

function rootsOfPage(page) {
  var out = [];
  function add(list) {
    for (var i = 0; i < list.length; i++) {
      var n = list[i];
      if (!n.visible) continue;
      if (n.type === 'SECTION') add(n.children);
      else if (n.type === 'FRAME' || n.type === 'COMPONENT' || n.type === 'COMPONENT_SET' || n.type === 'INSTANCE' || n.type === 'GROUP') out.push(n);
    }
  }
  add(page.children);
  return out;
}

function pageOf(node) {
  var n = node;
  while (n && n.type !== 'PAGE') n = n.parent;
  return n;
}

async function exportTokens() {
  var tokens = { paintStyles: [], textStyles: [], effectStyles: [], variableCollections: [], variables: [] };
  try {
    var ps = await figma.getLocalPaintStylesAsync();
    tokens.paintStyles = ps.map(function (s) { return { id: s.id, name: s.name, paints: plain(s.paints) }; });
    var ts = await figma.getLocalTextStylesAsync();
    tokens.textStyles = ts.map(function (s) {
      return { id: s.id, name: s.name, fontName: plain(s.fontName), fontSize: s.fontSize, lineHeight: plain(s.lineHeight),
        letterSpacing: plain(s.letterSpacing), textCase: plain(s.textCase), textDecoration: plain(s.textDecoration) };
    });
    var es = await figma.getLocalEffectStylesAsync();
    tokens.effectStyles = es.map(function (s) { return { id: s.id, name: s.name, effects: plain(s.effects) }; });
  } catch (e) { state.warnings.push('Styles: ' + e); }
  try {
    if (figma.variables) {
      var cols = await figma.variables.getLocalVariableCollectionsAsync();
      tokens.variableCollections = cols.map(function (c) { return { id: c.id, name: c.name, modes: plain(c.modes), defaultModeId: c.defaultModeId }; });
      var vars = await figma.variables.getLocalVariablesAsync();
      tokens.variables = vars.map(function (v) {
        return { id: v.id, name: v.name, collectionId: v.variableCollectionId, resolvedType: v.resolvedType, valuesByMode: plain(v.valuesByMode) };
      });
    }
  } catch (e) { state.warnings.push('Variables: ' + e); }
  return tokens;
}

async function runExport(opts) {
  resetState();
  var roots = [];
  if (opts.scope === 'selection') {
    roots = figma.currentPage.selection.filter(function (n) { return n.visible; });
    if (!roots.length) throw new Error('Kai select karyu nathi. Frame select karo ke scope badlo.');
  } else if (opts.scope === 'page') {
    roots = rootsOfPage(figma.currentPage);
  } else {
    progress('Loading all pages...');
    await figma.loadAllPagesAsync();
    for (var p = 0; p < figma.root.children.length; p++) roots = roots.concat(rootsOfPage(figma.root.children[p]));
  }
  if (!roots.length) throw new Error('Export karva mate koi frame malyu nahi.');

  var manifest = {
    tool: 'figma-export-kit',
    version: 1,
    fileName: figma.root.name,
    exportedAt: new Date().toISOString(),
    scope: opts.scope,
    frames: [],
    images: {},
    fonts: [],
    warnings: []
  };

  for (var i = 0; i < roots.length; i++) {
    var root = roots[i];
    var page = pageOf(root);
    var pageName = safeName(page ? page.name : 'Page');
    progress('Frame ' + (i + 1) + '/' + roots.length + ': ' + root.name);
    var json = await serialize(root, 0);
    if (!json) continue;
    var base = safeName(root.name);
    var jsonPath = uniquePath('pages/' + pageName, base, '.json');
    send(jsonPath, JSON.stringify(json, null, 2));
    var entry = { id: root.id, name: root.name, page: page ? page.name : '', type: root.type,
      width: root.width, height: root.height, json: jsonPath };
    if (opts.renders) {
      try {
        var rs = opts.renderScale || 2;
        var png = await root.exportAsync({ format: 'PNG', constraint: { type: 'SCALE', value: rs } });
        var renderPath = uniquePath('renders/' + pageName, base, '.png');
        send(renderPath, png);
        entry.render = renderPath;
        entry.renderScale = rs;
        manifest.renderScale = rs;
      } catch (e) { state.warnings.push('Render failed for "' + root.name + '": ' + e); }
    }
    manifest.frames.push(entry);
  }

  var hashes = Object.keys(state.imageHashes);
  for (var h = 0; h < hashes.length; h++) {
    progress('Images ' + (h + 1) + '/' + hashes.length);
    try {
      var img = figma.getImageByHash(hashes[h]);
      if (!img) continue;
      var bytes = await img.getBytesAsync();
      var size = await img.getSizeAsync();
      var path = 'assets/images/' + hashes[h] + imageExt(bytes);
      send(path, bytes);
      manifest.images[hashes[h]] = { path: path, width: size.width, height: size.height };
    } catch (e) { state.warnings.push('Image ' + hashes[h] + ': ' + e); }
  }

  progress('Design tokens...');
  send('tokens.json', JSON.stringify(await exportTokens(), null, 2));

  manifest.fonts = Object.keys(state.fonts).map(function (k) { return state.fonts[k]; });
  manifest.warnings = state.warnings;
  manifest.stats = { frames: manifest.frames.length, nodes: state.nodeCount, images: hashes.length };
  send('manifest.json', JSON.stringify(manifest, null, 2));

  figma.ui.postMessage({ type: 'done', zipName: safeName(figma.root.name) + '-export', stats: manifest.stats, warnings: state.warnings });
}

figma.ui.onmessage = async function (msg) {
  if (msg.type === 'export') {
    try {
      await runExport(msg);
    } catch (e) {
      figma.ui.postMessage({ type: 'error', text: String(e && e.message ? e.message : e) });
    }
  } else if (msg.type === 'cancel') {
    state.cancelled = true;
  } else if (msg.type === 'close') {
    figma.closePlugin();
  }
};
