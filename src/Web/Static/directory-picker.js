/**
 * Reusable directory picker component.
 *
 * Modes:
 *   'multi'  — click to toggle selection, tags shown above the list
 *   'single' — click to select (replaces previous), selected path shown above
 *
 * Usage:
 *   var picker = new DirectoryPicker({
 *       containerId: 'my-picker',       // wrapper element ID
 *       hiddenName:  'library.paths',   // form input name (multi creates multiple inputs)
 *       mode:        'multi',           // 'multi' | 'single'
 *       initialPath: '/media',          // starting browse path
 *       initial:     ['/media/anime'],  // pre-selected paths (array for multi, string or array for single)
 *       onChange:     function(paths) {} // optional callback with current selection
 *   });
 *   picker.init();
 *
 * Exposes: picker.getSelected(), picker.setSelected(paths)
 *
 * Depends on: /api/fs/browse endpoint, window.escHtml
 */
function DirectoryPicker(opts) {
    'use strict';

    var esc = window.escHtml || function (s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    };

    var mode = opts.mode || 'single';
    var container = null;
    var browsePath = opts.initialPath || '/media';
    var selected = [];
    var clickTimers = {};
    var self = this;
    var instanceId = opts.containerId || ('dp-' + Math.random().toString(36).slice(2, 8));

    // Pre-populate selections
    if (opts.initial) {
        if (Array.isArray(opts.initial)) {
            selected = opts.initial.filter(Boolean);
        } else if (opts.initial) {
            selected = [opts.initial];
        }
    }

    function el(id) { return document.getElementById(id); }

    // ── Build DOM ──────────────────────────────────────────
    function buildMarkup() {
        container = el(opts.containerId);
        if (!container) return;
        container.innerHTML =
            '<div class="dp-selection" id="' + instanceId + '-sel"></div>' +
            '<div class="dp-hidden-inputs" id="' + instanceId + '-inputs"></div>' +
            '<div class="file-browser" style="margin-top:0.5rem">' +
                '<div class="file-browser-toolbar">' +
                    '<button type="button" class="btn btn-secondary btn-sm" id="' + instanceId + '-parent"' +
                    ' style="font-size:0.78rem;padding:0.2rem 0.55rem">&uarr; Parent</button>' +
                    '<span class="file-browser-path" id="' + instanceId + '-path">' + esc(browsePath) + '</span>' +
                '</div>' +
                '<div class="file-browser-list" id="' + instanceId + '-list" style="max-height:200px;overflow-y:auto"></div>' +
            '</div>';

        el(instanceId + '-parent').addEventListener('click', function () {
            var parent = browsePath.split('/').slice(0, -1).join('/') || '/';
            browsePath = parent;
            browse(parent);
        });

        renderSelection();
        syncHiddenInputs();
    }

    // ── Selection rendering ────────────────────────────────
    function renderSelection() {
        var selEl = el(instanceId + '-sel');
        if (!selEl) return;

        if (mode === 'multi') {
            if (selected.length === 0) {
                selEl.innerHTML = '<span class="text-muted" style="font-size:0.82rem">No directories selected. Click to select, double-click to navigate.</span>';
            } else {
                selEl.innerHTML = '<div class="tag-list">' + selected.map(function (p, i) {
                    return '<span class="tag">' + esc(p) +
                        '<button type="button" class="tag-remove" data-idx="' + i + '" title="Remove">\u00d7</button></span>';
                }).join('') + '</div>';
                selEl.querySelectorAll('.tag-remove').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        var idx = parseInt(btn.dataset.idx);
                        selected.splice(idx, 1);
                        renderSelection();
                        syncHiddenInputs();
                        refreshVisuals();
                        fireChange();
                    });
                });
            }
        } else {
            // single mode
            if (selected.length === 0) {
                selEl.innerHTML = '<span class="text-muted" style="font-size:0.82rem">No directory selected. Click to select, double-click to navigate.</span>';
            } else {
                selEl.innerHTML = '<div style="font-size:0.85rem;color:var(--accent)"><strong>Selected:</strong> ' + esc(selected[0]) + '</div>';
            }
        }
    }

    function syncHiddenInputs() {
        var inputsEl = el(instanceId + '-inputs');
        if (!inputsEl || !opts.hiddenName) return;
        // Clear existing
        inputsEl.innerHTML = '';
        if (selected.length === 0) {
            // Ensure at least one empty hidden input so the form key exists
            inputsEl.innerHTML = '<input type="hidden" name="' + esc(opts.hiddenName) + '" value="">';
            return;
        }
        selected.forEach(function (p) {
            var inp = document.createElement('input');
            inp.type = 'hidden';
            inp.name = opts.hiddenName;
            inp.value = p;
            inputsEl.appendChild(inp);
        });
    }

    function fireChange() {
        if (typeof opts.onChange === 'function') {
            opts.onChange(selected.slice());
        }
    }

    // ── Browse ─────────────────────────────────────────────
    function renderItem(fullPath, name, isSel) {
        return '<div class="file-browser-item' + (isSel ? ' selected' : '') +
            '" data-path="' + esc(fullPath) + '">' +
            '<span class="dir-icon">' + (isSel ? '\u2713' : '\uD83D\uDCC1') + '</span>' +
            '<span class="dir-name">' + esc(name) + '</span>' +
            '<span class="dir-hint">double-click to open</span></div>';
    }

    function refreshVisuals() {
        var listEl = el(instanceId + '-list');
        if (!listEl) return;
        listEl.querySelectorAll('.file-browser-item').forEach(function (row) {
            var p = row.dataset.path;
            var isSel = selected.indexOf(p) !== -1;
            row.classList.toggle('selected', isSel);
            var icon = row.querySelector('.dir-icon');
            if (icon) icon.textContent = isSel ? '\u2713' : '\uD83D\uDCC1';
        });
    }

    async function browse(path) {
        var listEl = el(instanceId + '-list');
        if (!listEl) return;
        listEl.innerHTML = '<div class="file-browser-empty">Loading\u2026</div>';

        try {
            var resp = await fetch('/api/fs/browse?path=' + encodeURIComponent(path));
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            el(instanceId + '-path').textContent = data.current || path;

            var dirs = data.dirs || [];
            if (dirs.length === 0) {
                listEl.innerHTML = '<div class="file-browser-empty">No subdirectories here.</div>';
                return;
            }

            listEl.innerHTML = dirs.map(function (d) {
                var fullPath = d.path || (path.replace(/\/$/, '') + '/' + d.name);
                var isSel = selected.indexOf(fullPath) !== -1;
                return renderItem(fullPath, d.name, isSel);
            }).join('');

            attachHandlers(listEl);
        } catch (e) {
            listEl.innerHTML = '<div class="file-browser-empty" style="color:var(--danger)">Error: ' + esc(e.message) + '</div>';
        }
    }

    function attachHandlers(listEl) {
        listEl.querySelectorAll('.file-browser-item').forEach(function (row) {
            var rowPath = row.dataset.path;
            row.addEventListener('click', function () {
                if (clickTimers[rowPath]) return;
                clickTimers[rowPath] = setTimeout(function () {
                    delete clickTimers[rowPath];
                    toggleSelect(rowPath);
                }, 220);
            });
            row.addEventListener('dblclick', function () {
                if (clickTimers[rowPath]) {
                    clearTimeout(clickTimers[rowPath]);
                    delete clickTimers[rowPath];
                }
                // Deselect if navigating into a selected dir
                var idx = selected.indexOf(rowPath);
                if (idx !== -1) {
                    selected.splice(idx, 1);
                    renderSelection();
                    syncHiddenInputs();
                    fireChange();
                }
                browsePath = rowPath;
                browse(rowPath);
            });
        });
    }

    function toggleSelect(path) {
        if (mode === 'multi') {
            var idx = selected.indexOf(path);
            if (idx === -1) {
                selected.push(path);
            } else {
                selected.splice(idx, 1);
            }
        } else {
            // single: replace
            if (selected[0] === path) {
                selected = [];
            } else {
                selected = [path];
            }
        }
        renderSelection();
        syncHiddenInputs();
        refreshVisuals();
        fireChange();
    }

    // ── Public API ─────────────────────────────────────────

    this.init = function () {
        buildMarkup();
        browse(browsePath);
    };

    this.getSelected = function () {
        return mode === 'single' ? (selected[0] || null) : selected.slice();
    };

    this.setSelected = function (paths) {
        if (Array.isArray(paths)) {
            selected = paths.filter(Boolean);
        } else if (paths) {
            selected = [paths];
        } else {
            selected = [];
        }
        renderSelection();
        syncHiddenInputs();
        refreshVisuals();
        fireChange();
    };

    this.getMode = function () { return mode; };
    this.getBrowsePath = function () { return browsePath; };
}
