/**
 * Shared naming-template preview, presets, and chip handling.
 *
 * Expects element IDs:
 *   tpl-episode, tpl-folder, tpl-season, tpl-movie
 *   tpl-preview-file, tpl-preview-folder, tpl-preview-season, tpl-preview-movie
 *   .tpl-input  (class on all template inputs)
 *   .tpl-preset-chip[data-preset]  (class on preset buttons)
 *
 * Exposes: window.NamingTemplates = { PRESETS, renderTpl, updatePreview, init,
 *          getSelectedTitlePref, highlightMatchingChip, applyPreset }
 */
(function () {
    'use strict';

    var SAMPLE_TOKENS = {
        'title': 'Attack on Titan', 'title.romaji': 'Shingeki no Kyojin',
        'title.english': 'Attack on Titan', 'year': '2013', 'season': '01',
        'episode': '05', 'episode.title': '', 'quality': '1080p BluRay x265',
        'quality.resolution': '1080p', 'quality.source': 'BluRay',
        'season.name': 'Shingeki no Kyojin Season 2', 'format': 'TV', 'format.short': 'TV'
    };

    var MOVIE_TOKENS = {
        'title': 'Sen to Chihiro no Kamikakushi', 'title.romaji': 'Sen to Chihiro no Kamikakushi',
        'title.english': 'Spirited Away', 'year': '2001', 'season': '01',
        'episode': '', 'episode.title': '', 'quality': '1080p BluRay x265',
        'quality.resolution': '1080p', 'quality.source': 'BluRay',
        'season.name': '', 'format': 'MOVIE', 'format.short': 'Movie'
    };

    var PRESETS = {
        standard:              { file: '{title} - S{season}E{episode}', folder: '{title}', season_folder: 'Season {season}', movie_file: '{title} [{year}]', title_pref: 'romaji' },
        with_year:             { file: '{title} [{year}] - S{season}E{episode}', folder: '{title} [{year}]', season_folder: 'Season {season}', movie_file: '{title} [{year}]', title_pref: 'romaji' },
        with_quality:          { file: '{title} [{year}] - S{season}E{episode} [{quality}]', folder: '{title} [{year}]', season_folder: 'Season {season}', movie_file: '{title} [{year}] [{quality}]', title_pref: 'romaji' },
        english:               { file: '{title.english} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.english} ({year})', season_folder: 'Season {season}', movie_file: '{title.english} ({year}) [{quality}]', title_pref: 'english' },
        english_season_names:  { file: '{title.english} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.english} ({year})', season_folder: '{season.name} ({year})', movie_file: '{title.english} ({year}) [{quality}]', title_pref: 'english' },
        romaji:                { file: '{title.romaji} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.romaji} ({year})', season_folder: 'Season {season}', movie_file: '{title.romaji} ({year}) [{quality}]', title_pref: 'romaji' },
        romaji_season_names:   { file: '{title.romaji} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.romaji} ({year})', season_folder: '{season.name} ({year})', movie_file: '{title.romaji} ({year}) [{quality}]', title_pref: 'romaji' },
        dots:                  { file: '{title}.S{season}E{episode}.{quality.resolution}', folder: '{title}.({year})', season_folder: 'Season.{season}', movie_file: '{title}.({year})', title_pref: 'romaji' },
    };

    function renderTpl(template, tokens) {
        var r = template.replace(/\{([a-z]+(?:\.[a-z]+)?)\}/g, function (m, k) { return tokens[k] || ''; });
        r = r.replace(/\[\s*\]/g, '').replace(/\(\s*\)/g, '');
        r = r.replace(/(\s*-\s*){2,}/g, ' - ').replace(/\.{2,}/g, '.').replace(/ {2,}/g, ' ');
        return r.replace(/[\s.\-]+$/, '').replace(/^[\s.\-]+/, '').trim();
    }

    function updatePreview() {
        var ep = document.getElementById('tpl-episode');
        var fo = document.getElementById('tpl-folder');
        var se = document.getElementById('tpl-season');
        var mo = document.getElementById('tpl-movie');
        if (!ep || !fo || !se) return;

        var pFile   = document.getElementById('tpl-preview-file');
        var pFolder = document.getElementById('tpl-preview-folder');
        var pSeason = document.getElementById('tpl-preview-season');
        var pMovie  = document.getElementById('tpl-preview-movie');
        if (pFile)   pFile.textContent   = (renderTpl(ep.value, SAMPLE_TOKENS) || ep.placeholder) + '.mkv';
        if (pFolder) pFolder.textContent = renderTpl(fo.value, SAMPLE_TOKENS) || fo.placeholder;
        if (pSeason) pSeason.textContent = renderTpl(se.value, SAMPLE_TOKENS) || se.placeholder;
        if (pMovie && mo) pMovie.textContent = (renderTpl(mo.value, MOVIE_TOKENS) || mo.placeholder) + '.mkv';
    }

    var selectedTitlePref = '';

    /**
     * Check if the current form values match any preset and highlight that chip.
     * Also accepts an optional savedTitlePref to include in the match check.
     */
    function highlightMatchingChip(savedTitlePref) {
        var ep = (document.getElementById('tpl-episode') || {}).value || '';
        var fo = (document.getElementById('tpl-folder') || {}).value || '';
        var se = (document.getElementById('tpl-season') || {}).value || '';
        var mo = (document.getElementById('tpl-movie') || {}).value || '';
        var pref = savedTitlePref || selectedTitlePref || '';

        document.querySelectorAll('.tpl-preset-chip').forEach(function (c) {
            c.classList.remove('active');
        });

        var keys = Object.keys(PRESETS);
        for (var i = 0; i < keys.length; i++) {
            var p = PRESETS[keys[i]];
            if (p.file === ep && p.folder === fo && p.season_folder === se && p.movie_file === mo) {
                // If we have a title_pref to check, also verify it matches
                if (pref && p.title_pref !== pref) continue;
                var chip = document.querySelector('.tpl-preset-chip[data-preset="' + keys[i] + '"]');
                if (chip) {
                    chip.classList.add('active');
                    selectedTitlePref = p.title_pref;
                }
                break;
            }
        }
    }

    /**
     * Programmatically apply a preset by name and highlight its chip.
     */
    function applyPreset(presetName) {
        var p = PRESETS[presetName];
        if (!p) return;
        var ep = document.getElementById('tpl-episode');
        var fo = document.getElementById('tpl-folder');
        var se = document.getElementById('tpl-season');
        var mo = document.getElementById('tpl-movie');
        if (ep) ep.value = p.file;
        if (fo) fo.value = p.folder;
        if (se) se.value = p.season_folder;
        if (mo) mo.value = p.movie_file;
        selectedTitlePref = p.title_pref || '';
        document.querySelectorAll('.tpl-preset-chip').forEach(function (c) {
            c.classList.remove('active');
        });
        var chip = document.querySelector('.tpl-preset-chip[data-preset="' + presetName + '"]');
        if (chip) chip.classList.add('active');
        updatePreview();
    }

    /** Wire up .tpl-input listeners and .tpl-preset-chip click handlers. */
    function init(opts) {
        opts = opts || {};

        document.querySelectorAll('.tpl-input').forEach(function (inp) {
            inp.addEventListener('input', function () {
                updatePreview();
                // When the user manually edits, re-check chip highlighting
                highlightMatchingChip(selectedTitlePref);
            });
        });

        document.querySelectorAll('.tpl-preset-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                applyPreset(chip.dataset.preset);
            });
        });

        // If savedTitlePref is provided, use it to detect the active chip
        if (opts.savedTitlePref) {
            selectedTitlePref = opts.savedTitlePref;
        }

        // If a default preset is specified and all fields are empty, apply it
        if (opts.defaultPreset) {
            var ep = document.getElementById('tpl-episode');
            var fo = document.getElementById('tpl-folder');
            if (ep && fo && !ep.value && !fo.value) {
                applyPreset(opts.defaultPreset);
                return;
            }
        }

        // Otherwise, try to highlight the matching chip based on current values
        highlightMatchingChip(opts.savedTitlePref || '');
        updatePreview();
    }

    window.NamingTemplates = {
        PRESETS: PRESETS,
        renderTpl: renderTpl,
        updatePreview: updatePreview,
        init: init,
        getSelectedTitlePref: function () { return selectedTitlePref; },
        highlightMatchingChip: highlightMatchingChip,
        applyPreset: applyPreset,
    };
}());
