/**
 * Shared dual-pane file browser (source multi-select + output single-select).
 *
 * Usage:
 *   var fb = new FileBrowser({
 *       srcListId: 'src-browser-list',
 *       srcPathId: 'src-current-path',
 *       outListId: 'out-browser-list',
 *       outPathId: 'out-current-path',
 *       sourceTagsId: 'source-tags',
 *       outSelectedId: 'out-selected-path',
 *       newFolderRowId: 'out-new-folder-row',
 *       newFolderInputId: 'out-new-folder-input',
 *       initialPath: '/media',
 *   });
 *   fb.init();
 *
 * Exposes: fb.state.sourceDirs, fb.state.outputDir
 *
 * Depends on: window.escHtml (from base.html)
 */
function FileBrowser(opts) {
    'use strict';

    var esc = window.escHtml || function (s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    };

    this.state = {
        sourceDirs: [],
        outputDir: null,
        srcBrowsePath: opts.initialPath || '/media',
        outBrowsePath: opts.initialPath || '/media',
    };

    var self = this;
    var srcClickTimers = {};
    var outClickTimers = {};

    function el(id) { return document.getElementById(id); }

    // ── Rendering ──────────────────────────────────────────

    function renderItem(fullPath, name, isSelected) {
        return '<div class="file-browser-item' + (isSelected ? ' selected' : '') +
            '" data-path="' + esc(fullPath) + '">' +
            '<span class="dir-icon">' + (isSelected ? '\u2713' : '\uD83D\uDCC1') + '</span>' +
            '<span class="dir-name">' + esc(name) + '</span>' +
            '<span class="dir-hint">double-click to open</span></div>';
    }

    /** Update selection visuals in-place without re-fetching from server. */
    function refreshSelectionState(listId, mode) {
        var listEl = el(listId);
        if (!listEl) return;
        listEl.querySelectorAll('.file-browser-item').forEach(function (row) {
            var p = row.dataset.path;
            var isSel = mode === 'multi'
                ? self.state.sourceDirs.indexOf(p) !== -1
                : self.state.outputDir === p;
            if (isSel) {
                row.classList.add('selected');
            } else {
                row.classList.remove('selected');
            }
            var icon = row.querySelector('.dir-icon');
            if (icon) icon.textContent = isSel ? '\u2713' : '\uD83D\uDCC1';
        });
    }

    // ── Browse (network fetch) ─────────────────────────────

    async function browsePath(path, listId, pathId, mode) {
        var listEl = el(listId);
        listEl.innerHTML = '<div class="file-browser-empty">Loading\u2026</div>';
        try {
            var resp = await fetch('/api/fs/browse?path=' + encodeURIComponent(path));
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            el(pathId).textContent = data.current || path;

            var dirs = data.dirs || [];
            if (dirs.length === 0) {
                listEl.innerHTML = '<div class="file-browser-empty">No subdirectories here.</div>';
                return;
            }

            listEl.innerHTML = dirs.map(function (d) {
                var fullPath = d.path || (path.replace(/\/$/, '') + '/' + d.name);
                var isSel = mode === 'multi'
                    ? self.state.sourceDirs.indexOf(fullPath) !== -1
                    : self.state.outputDir === fullPath;
                return renderItem(fullPath, d.name, isSel);
            }).join('');

            attachHandlers(listEl, mode);
        } catch (e) {
            listEl.innerHTML = '<div class="file-browser-empty" style="color:var(--danger)">Error: ' + esc(e.message) + '</div>';
        }
    }

    function attachHandlers(listEl, mode) {
        var timers = mode === 'single' ? outClickTimers : srcClickTimers;
        listEl.querySelectorAll('.file-browser-item').forEach(function (row) {
            var rowPath = row.dataset.path;
            row.addEventListener('click', function () {
                if (timers[rowPath]) return;
                timers[rowPath] = setTimeout(function () {
                    delete timers[rowPath];
                    if (mode === 'single') {
                        self.state.outputDir = rowPath;
                        el(opts.outSelectedId).textContent = rowPath;
                        refreshSelectionState(opts.outListId, 'single');
                    } else {
                        srcToggleSelect(rowPath);
                    }
                }, 220);
            });
            row.addEventListener('dblclick', function () {
                if (timers[rowPath]) {
                    clearTimeout(timers[rowPath]);
                    delete timers[rowPath];
                }
                if (mode === 'single') {
                    if (self.state.outputDir === rowPath) {
                        self.state.outputDir = null;
                        el(opts.outSelectedId).textContent = 'none';
                    }
                    self.state.outBrowsePath = rowPath;
                    browsePath(rowPath, opts.outListId, opts.outPathId, 'single');
                } else {
                    var idx = self.state.sourceDirs.indexOf(rowPath);
                    if (idx !== -1) {
                        self.state.sourceDirs.splice(idx, 1);
                        renderSourceTags();
                    }
                    self.state.srcBrowsePath = rowPath;
                    browsePath(rowPath, opts.srcListId, opts.srcPathId, 'multi');
                }
            });
        });
    }

    // ── Source selection ────────────────────────────────────

    function srcToggleSelect(path) {
        var idx = self.state.sourceDirs.indexOf(path);
        if (idx === -1) {
            self.state.sourceDirs.push(path);
        } else {
            self.state.sourceDirs.splice(idx, 1);
        }
        renderSourceTags();
        refreshSelectionState(opts.srcListId, 'multi');
    }

    function renderSourceTags() {
        var container = el(opts.sourceTagsId);
        if (!container) return;
        if (self.state.sourceDirs.length === 0) {
            container.innerHTML = '<span class="text-muted" style="font-size:0.82rem">No sources added yet.</span>';
            return;
        }
        container.innerHTML = self.state.sourceDirs.map(function (p, i) {
            return '<span class="tag">' + esc(p) +
                '<button class="tag-remove" onclick="window._fb.removeSource(' + i + ')" title="Remove">\u00d7</button></span>';
        }).join('');
    }

    this.removeSource = function (i) {
        self.state.sourceDirs.splice(i, 1);
        renderSourceTags();
        refreshSelectionState(opts.srcListId, 'multi');
    };

    // ── Navigation ─────────────────────────────────────────

    this.srcBrowseParent = function () {
        var parent = self.state.srcBrowsePath.split('/').slice(0, -1).join('/') || '/';
        self.state.srcBrowsePath = parent;
        browsePath(parent, opts.srcListId, opts.srcPathId, 'multi');
    };

    this.outBrowseParent = function () {
        var parent = self.state.outBrowsePath.split('/').slice(0, -1).join('/') || '/';
        self.state.outBrowsePath = parent;
        browsePath(parent, opts.outListId, opts.outPathId, 'single');
    };

    // ── New folder ─────────────────────────────────────────

    this.outShowNewFolder = function () {
        var row = el(opts.newFolderRowId);
        if (row) {
            row.style.display = 'flex';
            var input = el(opts.newFolderInputId);
            if (input) { input.value = ''; input.focus(); }
        }
    };

    this.outHideNewFolder = function () {
        var row = el(opts.newFolderRowId);
        if (row) row.style.display = 'none';
        var input = el(opts.newFolderInputId);
        if (input) input.value = '';
    };

    this.outNewFolderKey = function (e) {
        if (e.key === 'Enter') self.outCreateFolder();
        if (e.key === 'Escape') self.outHideNewFolder();
    };

    this.outCreateFolder = async function () {
        var input = el(opts.newFolderInputId);
        var name = input ? input.value.trim() : '';
        if (!name) return;
        try {
            var resp = await fetch('/api/fs/mkdir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent: self.state.outBrowsePath, name: name }),
            });
            var data = await resp.json();
            if (!resp.ok || data.error) {
                alert('Could not create folder: ' + (data.error || resp.status));
                return;
            }
            self.outHideNewFolder();
            self.state.outputDir = data.path;
            el(opts.outSelectedId).textContent = data.path;
            self.state.outBrowsePath = data.path;
            browsePath(data.path, opts.outListId, opts.outPathId, 'single');
        } catch (e) {
            alert('Request failed: ' + e.message);
        }
    };

    // ── Public helpers ─────────────────────────────────────

    this.renderSourceTags = renderSourceTags;

    this.browseSrc = function (path) {
        self.state.srcBrowsePath = path;
        browsePath(path, opts.srcListId, opts.srcPathId, 'multi');
    };

    /**
     * Return effective source dirs: explicit selections, or the current
     * browse directory as an implicit fallback.  When the fallback is used,
     * the browse path is promoted into sourceDirs and the UI is updated.
     */
    this.getEffectiveSourceDirs = function () {
        if (self.state.sourceDirs.length > 0) return self.state.sourceDirs;
        if (self.state.srcBrowsePath && self.state.srcBrowsePath !== '/') {
            self.state.sourceDirs.push(self.state.srcBrowsePath);
            renderSourceTags();
            refreshSelectionState(opts.srcListId, 'multi');
        }
        return self.state.sourceDirs;
    };

    /**
     * Return effective output dir: explicit selection, or the current
     * browse directory as an implicit fallback.
     */
    this.getEffectiveOutputDir = function () {
        if (self.state.outputDir) return self.state.outputDir;
        self.state.outputDir = self.state.outBrowsePath;
        el(opts.outSelectedId).textContent = self.state.outBrowsePath;
        refreshSelectionState(opts.outListId, 'single');
        return self.state.outputDir;
    };

    /** Start both browsers. */
    this.init = function () {
        browsePath(self.state.srcBrowsePath, opts.srcListId, opts.srcPathId, 'multi');
        browsePath(self.state.outBrowsePath, opts.outListId, opts.outPathId, 'single');
        renderSourceTags();
        // Expose for onclick handlers in tags
        window._fb = self;
    };
}
