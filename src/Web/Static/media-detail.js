/**
 * Unified media detail modal.
 *
 * Shows a consistent detail card when clicking any media entry.
 * All pages show the same info; context only controls the toolbar
 * buttons (Edit Match for library, Refresh when matched).
 *
 * Depends on: escHtml (from base.html)
 */
(function () {
    'use strict';

    var esc = window.escHtml || function (s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    };
    function escJs(s) {
        return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }

    var STATUS_COLORS = {
        CURRENT: '#2e7d32', PLANNING: '#f9a825', COMPLETED: '#1565c0',
        PAUSED: '#546e7a', DROPPED: '#b71c1c', REPEATING: '#6a1b9a'
    };

    var SOURCE_COLORS = {
        local: '#3db4f2', plex: '#e5a00d', jellyfin: '#9147ff'
    };

    var TITLE_DISPLAY = 'romaji';

    window.setMediaDetailTitleDisplay = function (td) {
        TITLE_DISPLAY = td || 'romaji';
    };

    function resolveTitles(entry) {
        var romaji = entry.title_romaji || entry.anilist_title || '(untitled)';
        var english = entry.title_english || '';
        var primary, alt;
        if (TITLE_DISPLAY === 'english' || TITLE_DISPLAY === 'both_english_primary') {
            primary = english || romaji;
            alt = (romaji && romaji !== primary) ? romaji : '';
        } else {
            primary = romaji;
            alt = (english && english !== primary) ? english : '';
        }
        return { primary: primary, alt: alt };
    }

    function sourceChip(name) {
        var color = SOURCE_COLORS[name] || '#555';
        var label = name.charAt(0).toUpperCase() + name.slice(1);
        return '<span class="entry-detail-source-chip" style="background:' + color + '22;color:' + color + '">' +
            '<span class="source-dot" style="background:' + color + '"></span>' + esc(label) + '</span>';
    }

    /**
     * Determine whether entry is available locally / in a media server.
     * Works across different data shapes (watchlist enriched, library items, raw watchlist).
     */
    function hasLocally(entry) {
        if (entry.local_status === 'have' || entry.local_status === 'arr') return true;
        if (entry.sources && entry.sources.length > 0) return true;
        return false;
    }

    /**
     * Determine whether entry is already tracked in *arr.
     */
    function isTrackedInArr(entry) {
        return entry.arr_status && entry.arr_status !== 'untracked';
    }

    /**
     * Open the unified media detail modal.
     *
     * @param {Object} entry  - Media entry data
     * @param {string} context - "dashboard" | "watchlist" | "library"
     * @param {Object} [opts]  - { arrEnabled: bool }
     */
    window.openMediaDetail = function (entry, context, opts) {
        opts = opts || {};
        var modal = document.getElementById('mediaDetailModal');
        if (!modal) return;
        modal.classList.remove('hidden');

        var titles = resolveTitles(entry);

        // ── Header title ───────────────────────────────────
        document.getElementById('mdTitle').textContent = titles.primary;

        // ── Poster ─────────────────────────────────────────
        var posterWrap = document.getElementById('mdPosterWrap');
        if (entry.cover_image || entry.cover_url) {
            posterWrap.innerHTML = '<img src="' + esc(entry.cover_image || entry.cover_url) +
                '" class="entry-detail-poster" alt="">';
        } else {
            posterWrap.innerHTML = '<div class="entry-detail-poster-empty">?</div>';
        }

        // ── Title + AniList link ───────────────────────────
        var titleWrap = document.getElementById('mdTitleWrap');
        var altHtml = titles.alt
            ? '<div class="entry-detail-alt-title">' + esc(titles.alt) + '</div>'
            : '';
        if (entry.anilist_id) {
            titleWrap.innerHTML = '<a href="https://anilist.co/anime/' + entry.anilist_id +
                '" target="_blank" rel="noopener" class="entry-detail-title">' +
                esc(titles.primary) + '</a>' + altHtml;
        } else {
            titleWrap.innerHTML = '<div class="entry-detail-title" style="color:var(--text)">' +
                esc(titles.primary) + '</div>' + altHtml;
        }

        // ── Toolbar (top-right icon buttons) ───────────────
        var toolbar = document.getElementById('mdToolbar');
        var tbHtml = '';
        if (context === 'library' && typeof openMatchModal === 'function') {
            tbHtml += '<button onclick="closeMediaDetail();openMatchModal(' +
                (entry._idx || 0) + ')" title="Edit AniList match">&#x270E;</button>';
        }
        if (entry.anilist_id && context === 'library' && typeof refreshItem === 'function') {
            tbHtml += '<button onclick="closeMediaDetail();refreshItem(' +
                (entry._idx || 0) + ')" title="Refresh metadata from AniList">&#x21BB;</button>';
        }
        toolbar.innerHTML = tbHtml;

        // ── Info grid ──────────────────────────────────────
        var rows = [];

        // Watch status (available on watchlist/dashboard entries, and library if enriched)
        var ls = entry.list_status || '';
        if (ls) {
            rows.push(['Watch Status', '<span class="badge" style="background:' +
                (STATUS_COLORS[ls] || '#555') + ';color:#fff">' +
                ls.charAt(0) + ls.slice(1).toLowerCase() + '</span>']);
        }

        // Progress (directly below watch status)
        if (typeof entry.progress !== 'undefined' && entry.progress !== null) {
            var total = entry.anilist_episodes || entry.episodes;
            var progStr = total ? (entry.progress + '/' + total + ' eps') : (entry.progress + ' eps');
            rows.push(['Progress', progStr]);
        }

        // Airing
        var airing = entry.airing_status || entry.anilist_status || '';
        if (airing) {
            rows.push(['Airing', airing.replace(/_/g, ' ').toLowerCase()
                .replace(/\b\w/g, function (c) { return c.toUpperCase(); })]);
        }

        // Format
        if (entry.anilist_format) rows.push(['Format', entry.anilist_format]);

        // Year
        var year = entry.start_year || entry.year || '';
        if (year) rows.push(['Year', year]);

        // Episodes
        if (entry.anilist_episodes || entry.episodes) {
            rows.push(['Episodes', entry.anilist_episodes || entry.episodes]);
        }

        // *arr status
        if (isTrackedInArr(entry)) {
            rows.push(['*arr', (entry.arr_service || '') + ' ' +
                (entry.arr_status === 'monitored' ? '●' : '○')]);
        }

        // Sources — inline chips; show + Add only when NOT locally available AND NOT tracked
        var sourcesHtml = '';
        if (entry.sources && entry.sources.length) {
            // Library entries have an explicit sources array
            sourcesHtml = entry.sources.map(sourceChip).join('');
        } else if (entry.local_status === 'have') {
            sourcesHtml = sourceChip('local');
        } else if (entry.local_status === 'arr') {
            sourcesHtml = '<span class="entry-detail-source-chip" style="background:rgba(255,255,255,0.06);color:var(--text-muted)">via ' +
                esc(entry.arr_service || '*arr') + '</span>';
        }

        // Only show + Add when user does NOT have it locally and it's NOT already in *arr
        if (!hasLocally(entry) && !isTrackedInArr(entry) && opts.arrEnabled && entry.anilist_id) {
            sourcesHtml += (sourcesHtml ? ' ' : '') +
                '<button class="btn btn-secondary btn-xs" style="font-size:0.72rem;padding:0.12rem 0.5rem" ' +
                'onclick="closeMediaDetail();previewAdd(' + entry.anilist_id + ',\'' +
                escJs(entry.anilist_title || '') + '\',this)">+ Add to *arr</button>';
        }

        if (!sourcesHtml) {
            sourcesHtml = '<span style="color:var(--text-muted);font-size:0.82rem">None</span>';
        }
        rows.push(['Sources', sourcesHtml]);

        // Path (show when available)
        if (entry.folder_name || entry.folder_path) {
            rows.push(['Path', '<span style="font-family:monospace;font-size:0.78rem;word-break:break-all;white-space:normal">' +
                esc(entry.folder_path || entry.folder_name) + '</span>']);
        }

        var grid = document.getElementById('mdInfoGrid');
        grid.innerHTML = rows.map(function (r) {
            return '<span class="entry-detail-label">' + esc(r[0]) + '</span>' +
                '<span class="entry-detail-value">' + r[1] + '</span>';
        }).join('');
    };

    window.closeMediaDetail = function () {
        var modal = document.getElementById('mediaDetailModal');
        if (modal) modal.classList.add('hidden');
    };
})();
