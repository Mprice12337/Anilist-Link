/**
 * Shared naming-template preview, presets, and chip handling.
 *
 * Expects element IDs:
 *   tpl-episode, tpl-folder, tpl-season, tpl-movie
 *   tpl-preview-file, tpl-preview-folder, tpl-preview-season, tpl-preview-movie
 *   .tpl-input  (class on all template inputs)
 *   .tpl-preset-chip[data-preset]  (class on preset buttons)
 *
 * Exposes: window.NamingTemplates = { PRESETS, renderTpl, updatePreview, init }
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
        standard:              { file: '{title} - S{season}E{episode}', folder: '{title}', season_folder: 'Season {season}', movie_file: '{title} [{year}]' },
        with_year:             { file: '{title} [{year}] - S{season}E{episode}', folder: '{title} [{year}]', season_folder: 'Season {season}', movie_file: '{title} [{year}]' },
        with_quality:          { file: '{title} [{year}] - S{season}E{episode} [{quality}]', folder: '{title} [{year}]', season_folder: 'Season {season}', movie_file: '{title} [{year}] [{quality}]' },
        english:               { file: '{title.english} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.english} ({year})', season_folder: 'Season {season}', movie_file: '{title.english} ({year}) [{quality}]' },
        english_season_names:  { file: '{title.english} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.english} ({year})', season_folder: '{season.name} ({year})', movie_file: '{title.english} ({year}) [{quality}]' },
        romaji:                { file: '{title.romaji} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.romaji} ({year})', season_folder: 'Season {season}', movie_file: '{title.romaji} ({year}) [{quality}]' },
        romaji_season_names:   { file: '{title.romaji} ({year}) - S{season}E{episode} - {episode.title} [{quality}]', folder: '{title.romaji} ({year})', season_folder: '{season.name} ({year})', movie_file: '{title.romaji} ({year}) [{quality}]' },
        dots:                  { file: '{title}.S{season}E{episode}.{quality.resolution}', folder: '{title}.({year})', season_folder: 'Season.{season}', movie_file: '{title}.({year})' },
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

    /** Wire up .tpl-input listeners and .tpl-preset-chip click handlers. */
    function init() {
        document.querySelectorAll('.tpl-input').forEach(function (inp) {
            inp.addEventListener('input', updatePreview);
        });
        updatePreview();

        document.querySelectorAll('.tpl-preset-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var p = PRESETS[chip.dataset.preset];
                if (!p) return;
                document.getElementById('tpl-episode').value = p.file;
                document.getElementById('tpl-folder').value  = p.folder;
                document.getElementById('tpl-season').value  = p.season_folder;
                document.getElementById('tpl-movie').value   = p.movie_file;
                document.querySelectorAll('.tpl-preset-chip').forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                updatePreview();
            });
        });
    }

    window.NamingTemplates = { PRESETS: PRESETS, renderTpl: renderTpl, updatePreview: updatePreview, init: init };
}());
