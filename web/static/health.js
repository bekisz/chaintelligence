document.addEventListener('DOMContentLoaded', () => {
    const refreshBtn = document.getElementById('refresh-health-btn');
    const lastUpdatedText = document.getElementById('last-updated-text');
    const statusPulseEl = document.getElementById('system-status-pulse');
    const statusTitleEl = document.getElementById('system-status-title');
    const dbStatusBadgeEl = document.getElementById('db-status-badge');
    const healthScoreValEl = document.getElementById('health-score-value');
    const healthScoreFillEl = document.getElementById('health-score-fill');
    
    const totalSwapsEl = document.getElementById('total-swaps-count');
    const totalCoinsEl = document.getElementById('total-coins-count');
    const totalPoolsEl = document.getElementById('total-pools-count');
    const coinsSubtextEl = document.getElementById('coins-subtext');
    const coverageMainEl = document.getElementById('contract-coverage-main');
    const coverageSubtextEl = document.getElementById('contract-coverage-subtext');

    const chainsGridEl = document.getElementById('chains-health-grid');
    const summaryGridEl = document.getElementById('tables-summary-grid');
    const summaryBadgeCountEl = document.getElementById('summary-badge-count');
    const tablesDetailContainerEl = document.getElementById('tables-detail-container');
    const tableSearchInput = document.getElementById('table-search-input');
    const odsGoalStateContainerEl = document.getElementById('ods-goal-state-container');

    const apiModal = document.getElementById('api-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const modalUrlInput = document.getElementById('modal-url-input');
    const copyModalUrlBtn = document.getElementById('copy-modal-url-btn');
    const modalJsonViewer = document.getElementById('modal-json-viewer');

    let currentHealthData = null;

    window.currentMatrixVolFilter = window.currentMatrixVolFilter || '0';
    window.setMatrixVolumeFilter = function(val) {
        window.currentMatrixVolFilter = String(val);
        if (currentHealthData) {
            renderHealthUI(currentHealthData);
        }
    };

    const formatNumber = (num) => {
        if (num === undefined || num === null) return '--';
        return Number(num).toLocaleString();
    };

    let _taxonomyData = null;
    let _taxonomyMode = null;

    const formatDate = (isoStr) => {
        if (!isoStr) return '--';
        try {
            const date = new Date(isoStr);
            if (isNaN(date.getTime())) return String(isoStr);
            return date.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
        } catch (e) {
            return String(isoStr);
        }
    };

    const formatTimeAgo = (isoStr) => {
        if (!isoStr) return '';
        try {
            const date = new Date(isoStr);
            if (isNaN(date.getTime())) return '';
            const now = new Date();
            const diffSec = Math.floor((now - date) / 1000);
            if (diffSec < 0) return 'Just now';
            if (diffSec < 60) return `${diffSec}s ago`;
            if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
            if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
            return `${Math.floor(diffSec / 86400)}d ago`;
        } catch (e) {
            return '';
        }
    };

    const formatShortTimeAgo = (isoStr) => {
        if (!isoStr) return '--';
        try {
            const date = new Date(isoStr);
            if (isNaN(date.getTime())) return '--';
            const now = new Date();
            const diffSec = Math.floor((now - date) / 1000);
            if (diffSec < 0) return 'now';
            if (diffSec < 60) return `${diffSec}s`;
            if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
            if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
            return `${Math.floor(diffSec / 86400)}d`;
        } catch (e) {
            return '--';
        }
    };

    const ORDERED_TABLE_NAMES = [
        'swaps',
        'coin',
        'coin_contract',
        'coin_price_history',
        'route_taxonomy',
        'liquidity_pool',
        'liquidity_pool_daily_stats',
        'liquidity_pool_position',
        'liquidity_pool_position_event',
        'liquidity_pool_position_snapshot'
    ];

    const SECTION_GROUPS = [
        {
            id: 'swaps-group',
            title: 'Swaps Ingestion',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>',
            tables: ['swaps']
        },
        {
            id: 'coins-group',
            title: 'Coins',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v12M15 9.5H10.5a1.5 1.5 0 0 0 0 3h3a1.5 1.5 0 0 1 0 3H9"></path></svg>',
            tables: ['coin', 'coin_contract', 'coin_price_history']
        },
        {
            id: 'routes-group',
            title: 'Route Taxonomy',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="5" cy="12" r="2"></circle><circle cx="19" cy="5" r="2"></circle><circle cx="19" cy="19" r="2"></circle><path d="M7 12h5a5 5 0 0 0 5-5"></path><path d="M7 12h5a5 5 0 0 1 5 5"></path></svg>',
            tables: ['route_taxonomy']
        },
        {
            id: 'pools-group',
            title: 'Liquidity Pool',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>',
            tables: ['liquidity_pool', 'liquidity_pool_daily_stats']
        },
        {
            id: 'positions-group',
            title: 'Liquidity Pool Positions',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path></svg>',
            tables: ['liquidity_pool_position', 'liquidity_pool_position_event', 'liquidity_pool_position_snapshot']
        }
    ];

    const tableMetaMap = {
        'swaps': { title: 'Swaps Ingestion', category: 'Swaps Ingestion', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>', defaultPolicy: 'Latest swap per chain within 3 hours' },
        'coin': { title: 'Coins Metadata', category: 'Coins', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v12M15 9.5H10.5a1.5 1.5 0 0 0 0 3h3a1.5 1.5 0 0 1 0 3H9"></path></svg>', defaultPolicy: 'Metadata sync within 2 days' },
        'coin_contract': { title: 'Coin Contracts', category: 'Coins', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>', defaultPolicy: 'Multi-chain token address tracking' },
         'coin_price_history': { title: 'Coin Price History', category: 'Coins', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>', defaultPolicy: 'Daily candles within 2 days' },
         'route_taxonomy': { title: 'Route Taxonomy', category: 'Route Taxonomy', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="5" cy="12" r="2"></circle><circle cx="19" cy="5" r="2"></circle><circle cx="19" cy="19" r="2"></circle><path d="M7 12h5a5 5 0 0 0 5-5"></path><path d="M7 12h5a5 5 0 0 1 5 5"></path><path d="M7 12h5a5 5 0 0 1 5 5"></path></svg>', defaultPolicy: 'Route endpoint pairs, paths, and daily statistics' },
         'liquidity_pool': { title: 'Liquidity Pools', category: 'Liquidity Pool', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>', defaultPolicy: 'Active DEX pool registry' },
        'liquidity_pool_daily_stats': { title: 'Pool Daily Stats', category: 'Liquidity Pool', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>', defaultPolicy: 'Daily TVL & Volume metrics within 2 days' },
        'liquidity_pool_position': { title: 'LP Positions', category: 'Liquidity Pool Positions', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path></svg>', defaultPolicy: 'Tracked user NFT & pool positions' },
        'liquidity_pool_position_event': { title: 'LP Position Events', category: 'Liquidity Pool Positions', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polyline></svg>', defaultPolicy: 'On-chain mint, burn, collect logs' },
        'liquidity_pool_position_snapshot': { title: 'LP Position Snapshots', category: 'Liquidity Pool Positions', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>', defaultPolicy: 'Hourly snapshots within 2 days' }
    };

    const fetchHealthData = async () => {
        if (refreshBtn) refreshBtn.classList.add('rotating');
        try {
            const res = await fetch('/health');
            if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
            const data = await res.json();
            currentHealthData = data;
            renderHealthUI(data);
            fetchOdsReconciliation();
        } catch (err) {
            console.error('Failed to fetch health status:', err);
            if (statusTitleEl) {
                statusTitleEl.textContent = 'SYSTEM UNREACHABLE';
                statusTitleEl.className = 'status-heading text-danger';
            }
            if (statusPulseEl) statusPulseEl.className = 'status-pulse-ring pulse-red';
            if (dbStatusBadgeEl) {
                dbStatusBadgeEl.textContent = 'DISCONNECTED';
                dbStatusBadgeEl.className = 'kpi-value-badge status-error';
            }
        } finally {
            if (refreshBtn) refreshBtn.classList.remove('rotating');
        }
    };

    const renderHealthUI = (data) => {
        const sysStatus = (data.status || 'ok').toLowerCase();
        
        // 1. Overall Status
        if (sysStatus === 'ok') {
            statusTitleEl.textContent = 'ALL SYSTEMS OPERATIONAL';
            statusTitleEl.className = 'status-heading text-success';
            statusPulseEl.className = 'status-pulse-ring pulse-green';
        } else if (sysStatus === 'degraded') {
            statusTitleEl.textContent = 'PARTIAL INDEXER STALENESS';
            statusTitleEl.className = 'status-heading text-warning';
            statusPulseEl.className = 'status-pulse-ring pulse-yellow';
        } else {
            statusTitleEl.textContent = 'CRITICAL WAREHOUSE ISSUE';
            statusTitleEl.className = 'status-heading text-danger';
            statusPulseEl.className = 'status-pulse-ring pulse-red';
        }

        // DB Status
        const dbStatus = data.db ? data.db.status : 'disconnected';
        if (dbStatus === 'connected') {
            dbStatusBadgeEl.textContent = 'CONNECTED';
            dbStatusBadgeEl.className = 'kpi-value-badge status-ok';
        } else {
            dbStatusBadgeEl.textContent = String(dbStatus).toUpperCase();
            dbStatusBadgeEl.className = 'kpi-value-badge status-error';
        }

        // Timestamp
        if (lastUpdatedText) {
            lastUpdatedText.textContent = `Refreshed ${formatDate(data.timestamp)}`;
        }

        const tables = (data.db && data.db.table) ? data.db.table : {};
        const activeTables = ORDERED_TABLE_NAMES.filter(k => tables[k]);
        let freshCount = 0;
        activeTables.forEach(k => {
            const t = tables[k];
            let isStale = false;
            if (t.checks) {
                Object.values(t.checks).forEach(v => {
                    if (v === 'fail') isStale = true;
                });
            } else if (t.status === 'stale') {
                isStale = true;
            }
            if (!isStale) freshCount++;
        });

        const healthRatio = activeTables.length > 0 ? Math.round((freshCount / activeTables.length) * 100) : 100;
        if (healthScoreValEl) healthScoreValEl.textContent = `${healthRatio}% Operational (${freshCount}/${activeTables.length} Tables)`;
        if (healthScoreFillEl) {
            healthScoreFillEl.style.width = `${healthRatio}%`;
            healthScoreFillEl.className = healthRatio >= 90 ? 'health-meter-fill fill-green' : (healthRatio >= 70 ? 'health-meter-fill fill-yellow' : 'health-meter-fill fill-red');
        }

        // KPI values
        if (tables.swaps) totalSwapsEl.textContent = formatNumber(tables.swaps.count);
        if (tables.coin) {
            totalCoinsEl.textContent = formatNumber(tables.coin.count);
            if (tables.coin.contract_coverage) {
                const cov = tables.coin.contract_coverage;
                if (coverageMainEl) coverageMainEl.textContent = `${cov.any_chain_percentage}%`;
                if (coverageSubtextEl) {
                    coverageSubtextEl.textContent = `ETH: ${cov.ethereum_percentage}% | BNB: ${cov.bnb_percentage}% | Base: ${cov.base_percentage}% | Arb: ${cov.arbitrum_percentage}%`;
                }
                coinsSubtextEl.textContent = `${cov.any_chain_percentage}% Contract Mapped`;
            } else if (tables.coin.latest && tables.coin.latest.symbol) {
                coinsSubtextEl.textContent = `Latest: $${tables.coin.latest.symbol}`;
            }
        }
        if (tables.liquidity_pool) totalPoolsEl.textContent = formatNumber(tables.liquidity_pool.count);

        // Render TOP Amber-Green Summary Code Matrix
        renderSummaryMatrix(tables);

        // Render Grids
        renderChainsGrid(tables.swaps);
        renderTableDetailCards(tables);
    };

    // ===== O&D Set Requirements (goal-state coverage) =====

    const odsStatusMeta = {
        ok:      { dot: 'dot-green', fill: 'fill-green', pill: 'OK',         label: 'MET',     color: '#34d399' },
        partial: { dot: 'dot-amber', fill: 'fill-yellow', pill: 'PARTIAL',   label: 'PARTIAL', color: '#fbbf24' },
        missing: { dot: 'dot-red',   fill: 'fill-red',    pill: 'MISSING',   label: 'GAP',     color: '#f87171' },
        stale:   { dot: 'dot-amber', fill: 'fill-yellow', pill: 'STALE',     label: 'STALE',   color: '#fbbf24' }
    };

    // Token icons for the O&D Set Part column — same sources as the Swap page.
    let odsTokenImages = {};
    let odsFamilies = {};
    let lastOdsGoalData = null;

    const initOdsTokenIcons = () => {
        const loadImages = fetch('/api/coins/list')
            .then(r => r.json())
            .then(coins => {
                (Array.isArray(coins) ? coins : []).forEach(c => {
                    if (c.symbol) odsTokenImages[c.symbol.toUpperCase()] = c.image;
                });
            })
            .catch(() => {});
        const loadFamilies = fetch('/api/coin-families')
            .then(r => r.json())
            .then(data => {
                const fams = data && data.data ? (Array.isArray(data.data) ? data.data : [data.data]) : [];
                const includedById = {};
                (data.included || []).forEach(inc => { includedById[inc.type + ':' + inc.id] = inc; });
                fams.forEach(f => {
                    const famName = (f.attributes && f.attributes.name) || f.id;
                    const memberRefs = (f.relationships && f.relationships.members && f.relationships.members.data) || [];
                    const symbols = memberRefs.map(ref => {
                        const coin = includedById[ref.type + ':' + ref.id];
                        return coin && coin.attributes ? coin.attributes.symbol : null;
                    }).filter(Boolean);
                    odsFamilies[famName] = symbols;
                });
            })
            .catch(() => {});
        // Re-render once icons are available so the table isn't stuck showing
        // only tickers (it renders before these async fetches resolve).
        Promise.all([loadImages, loadFamilies]).then(() => {
            if (lastReconData) renderOdsReconciliation(lastReconData);
            else if (lastOdsGoalData) renderOdsGoalState(lastOdsGoalData);
        });
    };

    // Custom styled popover tooltip for .ods-tooltip elements.
    let odsTipEl = null;
    const escHtmlText = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    const initOdsTooltips = () => {
        if (!odsTipEl) {
            odsTipEl = document.createElement('div');
            odsTipEl.className = 'ods-pop-tooltip';
            document.body.appendChild(odsTipEl);
        }
        const box = odsGoalStateContainerEl;
        if (!box || box.dataset.tooltipsInit) return;
        box.dataset.tooltipsInit = '1';

        box.addEventListener('mouseover', (e) => {
            const t = e.target.closest && e.target.closest('.ods-tooltip');
            if (t) {
                let html = t.getAttribute('data-tip-html');
                const text = t.getAttribute('data-tip');
                if (!html && text) html = `<div class="ods-tt-simple">${escHtmlText(text)}</div>`;
                if (html) {
                    odsTipEl.innerHTML = html;
                    odsTipEl.classList.add('visible');
                }
            }
        });
        box.addEventListener('mousemove', (e) => {
            if (odsTipEl && odsTipEl.classList.contains('visible')) positionOdsTooltip(e);
        });
        box.addEventListener('mouseout', (e) => {
            if (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest('.ods-tooltip')) {
                if (odsTipEl) odsTipEl.classList.remove('visible');
            }
        });
    };

    const positionOdsTooltip = (e) => {
        const pad = 12;
        odsTipEl.style.left = '0px';
        odsTipEl.style.top = '0px';
        const r = odsTipEl.getBoundingClientRect();
        let x = e.clientX + 14;
        let y = e.clientY - r.height - 12;
        if (x + r.width > window.innerWidth - pad) x = e.clientX - r.width - 14;
        if (y < pad) y = e.clientY + 16;
        odsTipEl.style.left = `${x}px`;
        odsTipEl.style.top = `${y}px`;
    };

    const ODS_ARROW_SVGS = {
        forward: '<svg width="20" height="10" viewBox="0 0 40 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="12" x2="38" y2="12"></line><polyline points="28 5 38 12 28 19"></polyline></svg>',
        both: '<svg width="20" height="10" viewBox="0 0 40 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="12" x2="38" y2="12"></line><polyline points="8 5 2 12 8 19"></polyline><polyline points="32 5 38 12 32 19"></polyline></svg>'
    };

    const ODS_WILDCARD_ICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='%23facc15'%3E%3Cpolygon points='22,12 13.3,11.25 17,3.34 12,10.5 7,3.34 10.7,11.25 2,12 10.7,12.75 7,20.66 12,13.5 17,20.66 13.3,12.75'/%3E%3C/svg%3E";

    const odsTokenIcon = (spec, size = 14) => {
        if (!spec) return '';
        if (String(spec).toUpperCase() === '*') {
            return `<img src="${ODS_WILDCARD_ICON}" width="${size}" height="${size}" style="border-radius:2px; flex-shrink:0;">`;
        }
        const upper = String(spec).toUpperCase();
        if (/^0x/i.test(upper)) return '';
        let url = odsTokenImages[upper];
        if (!url && odsFamilies[upper] && odsFamilies[upper].length) {
            const member = odsFamilies[upper].find(m => odsTokenImages[(m || '').toUpperCase()]) || odsFamilies[upper][0];
            url = odsTokenImages[(member || '').toUpperCase()];
        }
        if (!url) {
            url = `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${upper.toLowerCase()}.png`;
        }
        return `<img src="${url}" width="${size}" height="${size}" onerror="this.style.visibility='hidden'" style="border-radius:50%; vertical-align:middle; flex-shrink:0;">`;
    };

    const odsSideKind = (spec) => {
        if (!spec) return 'token';
        const upper = String(spec).toUpperCase();
        if (upper === '*') return 'wild';
        if (/^0x/i.test(upper)) return 'contract';
        return (odsFamilies[upper] && odsFamilies[upper].length) ? 'family' : 'token';
    };

    const ODS_CHAIN_ICONS = {
        all: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%23ff007a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'%3E%3C/polygon%3E%3Cpolyline points='2 17 12 22 22 17'%3E%3C/polyline%3E%3Cpolyline points='2 12 12 17 22 12'%3E%3C/polyline%3E%3C/svg%3E",
        Ethereum: 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png',
        Arbitrum: 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/arbitrum/info/logo.png',
        Base: 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/info/logo.png',
        BNB: 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/binance/info/logo.png'
    };

    const odsSetPartHtml = (g) => {
        const arrow = ODS_ARROW_SVGS[g.bidirectional ? 'both' : 'forward'] || ODS_ARROW_SVGS.forward;
        const kindLabel = { family: 'coin family', token: 'token', contract: 'contract address', wild: 'wildcard' };
        const side = (spec, kind) => {
            const label = `${kindLabel[kind]}: ${spec || '--'}`;
            if (kind === 'wild') {
                // Wildcard side: icon only, no text.
                return `<span class="ods-tooltip" data-tip="${label}" style="display:inline-flex; align-items:center;">${odsTokenIcon(spec)}</span>`;
            }
            return `<span class="ods-tooltip" data-tip="${label}" style="display:inline-flex; align-items:center; gap:5px; white-space:nowrap;">
                ${odsTokenIcon(spec)}<span class="font-mono" style="color:#f3f4f6;">${spec || '--'}</span>
            </span>`;
        };
        return `<span style="display:inline-flex; align-items:center; gap:7px; white-space:nowrap;">
            ${side(g.origin, odsSideKind(g.origin))}
            <span style="color:#9ca3af; display:inline-flex;">${arrow}</span>
            ${side(g.dest, odsSideKind(g.dest))}
        </span>`;
    };

    const odsChainsHtml = (chains) => {
        const arr = Array.isArray(chains) && chains.length ? chains : [chains || '*'];
        const isAll = arr.length === 1 && arr[0] === '*';
        if (isAll) {
            return `<span class="ods-tooltip" data-tip="all chains" style="display:inline-flex; align-items:center;">
                <img src="${ODS_CHAIN_ICONS.all}" width="14" height="14" style="border-radius:2px; flex-shrink:0;">
            </span>`;
        }
        return `<span style="display:inline-flex; align-items:center; gap:8px; white-space:nowrap;">
            ${arr.map(c => {
                const key = Object.keys(ODS_CHAIN_ICONS).find(k => k.toLowerCase() === String(c).toLowerCase());
                const icon = key ? ODS_CHAIN_ICONS[key] : ODS_CHAIN_ICONS.all;
                return `<span class="ods-tooltip" data-tip="${c}" style="display:inline-flex; align-items:center;">
                    <img src="${icon}" width="14" height="14" onerror="this.src='/static/favicon.png'" style="border-radius:2px; flex-shrink:0;">
                </span>`;
            }).join('')}
        </span>`;
    };

    const fetchOdsGoalState = async () => {
        if (!odsGoalStateContainerEl) return;
        try {
            const res = await fetch('/api/ods/goal-state');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            lastOdsGoalData = data;
            renderOdsGoalState(data);
        } catch (err) {
            odsGoalStateContainerEl.innerHTML = `
                <div class="empty-state glass-card">
                    <div class="empty-state-title">Goal-state report unavailable</div>
                    <div class="empty-state-desc font-mono">${err.message}</div>
                </div>`;
        }
    };

    const renderOdsGoalState = (data) => {
        if (!odsGoalStateContainerEl) return;
        const checks = Array.isArray(data.checks) ? data.checks : [];
        const gaps = Array.isArray(data.gaps) ? data.gaps : [];
        const notOk = data.not_ok || 0;
        const total = data.n_checks || checks.length;
        const configPath = data.config_path || '';

        let html = '';

        // Header strip
        html += `
            <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
                <span class="summary-badge-pill badge-${notOk > 0 ? 'stale' : 'pass'}" style="font-size:0.78rem; padding:5px 12px;">
                    ${total - notOk}/${total} requirements met
                </span>
                <span class="summary-badge-pill badge-${notOk > 0 ? 'stale' : 'pass'}" style="font-size:0.78rem; padding:5px 12px;">
                    ${notOk > 0 ? `${notOk} not met` : 'fully satisfied'}
                </span>
                ${configPath ? `<span class="dim-text font-mono" style="font-size:0.72rem;">source: ${configPath}</span>` : ''}
            </div>
        `;

        if (checks.length === 0) {
            html += `<div class="empty-state glass-card"><div class="empty-state-title">No requirements declared</div><div class="empty-state-desc">Add requirements to config/ods-goal-state.yaml to see coverage here.</div></div>`;
            odsGoalStateContainerEl.innerHTML = html;
            return;
        }

        // Requirement status table (rows = requirement, columns = O&D set part + layers)
        const LAYERS = ['swaps', 'route_daily_stats', 'route_daily_stats_bucket', 'liquidity_pool_daily_stats', 'liquidity_pool_daily_stats_bucket'];

        const byName = new Map();
        checks.forEach(chk => {
            if (!byName.has(chk.name)) {
                byName.set(chk.name, { name: chk.name, origin: chk.origin, dest: chk.dest, bidirectional: chk.bidirectional, chains: chk.chains, layers: {} });
            }
            byName.get(chk.name).layers[chk.layer] = chk;
        });

        const odsStatusPill = (chk) => {
            if (!chk) return '<span class="dim-text" style="font-size:0.75rem;">—</span>';
            const meta = odsStatusMeta[chk.status] || odsStatusMeta.stale;
            const present = chk.present_days || 0;
            const expected = chk.expected_days || 0;
            const pct = expected > 0 ? Math.max(0, Math.min(100, Math.round((present / expected) * 100))) : 0;
            const MS = 86400000;
            const dates = (chk.missing_days || []).slice().sort();
            const ranges = [];
            if (dates.length) {
                let start = dates[0], prev = dates[0];
                for (let i = 1; i < dates.length; i++) {
                    if (new Date(dates[i]) - new Date(prev) === MS) { prev = dates[i]; }
                    else { ranges.push([start, prev]); start = prev = dates[i]; }
                }
                ranges.push([start, prev]);
            }
            let ttHtml;
            if (ranges.length) {
                const rows = ranges.map(([a, b]) => {
                    const n = Math.round((new Date(b) - new Date(a)) / MS) + 1;
                    return `<div class="ods-tt-row"><span class="ods-tt-days">${n}d</span><span>${a} → ${b}</span></div>`;
                }).join('');
                ttHtml = `<div class="ods-tt-header"><span class="ods-tt-dot"></span>Coverage Gaps</div><div class="ods-tt-body">${rows}</div>`;
            } else {
                ttHtml = `<div class="ods-tt-header"><span class="ods-tt-dot"></span>Coverage Gaps</div><div class="ods-tt-body"><div class="ods-tt-row"><span class="ods-tt-days ok">${present}/${expected}</span><span>days · fully covered</span></div></div>`;
            }
            const escAttr = s => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
            return `<span class="summary-badge-pill ods-tooltip" data-tip-html="${escAttr(ttHtml)}"
                style="color:${meta.color}; border-color:${meta.color}33; background:${meta.color}1a; font-size:0.75rem;">
                ${pct}%</span>`;
        };

        const odsWindowReq = (chk) => {
            if (!chk) return '<span class="dim-text" style="font-size:0.75rem;">—</span>';
            const label = chk.window_label || '—';
            const tip = `${label}\nwindow: ${chk.window ? chk.window.start : '--'} → ${chk.window ? chk.window.end : '--'}`;
            return `<span class="ods-tooltip font-mono dim-text" data-tip="${tip.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')}" style="font-size:0.72rem; white-space:nowrap;">${label}</span>`;
        };

        let tableHtml = `
            <table class="indexer-matrix-table ods-goal-state-table">
                <thead>
                    <tr>
                        <th rowspan="2" style="text-align:left;">Requirement</th>
                        <th rowspan="2" style="text-align:left;">O&amp;D Set Part</th>
                        <th rowspan="2" style="text-align:left;">Chain</th>
                        ${LAYERS.map(l => `<th colspan="2" style="text-align:center;">${l}</th>`).join('')}
                    </tr>
                    <tr>
                        ${LAYERS.map(() => `<th style="text-align:center;">req</th><th style="text-align:center;">%</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
        `;
        byName.forEach(g => {
            tableHtml += `
                <tr>
                    <td style="text-align:left; font-weight:700; color:#f3f4f6;">${g.name || '(unnamed requirement)'}</td>
                    <td style="text-align:left;">${odsSetPartHtml(g)}</td>
                    <td style="text-align:left;">${odsChainsHtml(g.chains)}</td>
                    ${LAYERS.map(l => `<td style="text-align:center;">${odsWindowReq(g.layers[l])}</td><td style="text-align:center;">${odsStatusPill(g.layers[l])}</td>`).join('')}
                </tr>
            `;
        });
        tableHtml += '</tbody></table>';

        html += tableHtml;

        // Gaps panel
        if (gaps.length > 0) {
            let gapsHtml = '';
            gaps.forEach(g => {
                gapsHtml += `
                    <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.78rem;">
                        <span class="summary-badge-pill badge-stale" style="color:#f87171; border-color:#f8717133; background:#f871711a;">${g.days}d</span>
                        <span class="font-mono" style="color:#f3f4f6;">${g.from} → ${g.to}</span>
                        <span class="dim-text font-mono">layer: ${g.layer}</span>
                        <span class="dim-text">${g.name}</span>
                        <span class="dim-text font-mono">chains: ${Array.isArray(g.chain) ? g.chain.join(', ') : g.chain}</span>
                    </div>
                `;
            });
            html += `
                <div class="ods-gaps-panel">
                    <div class="subpanel-title" style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                        <span class="status-indicator-dot dot-amber"></span>
                        <span style="font-weight:700; color:#f9a8d4;">Missing Coverage Gaps</span>
                        <span class="dim-text" style="font-size:0.75rem;">(${gaps.length} contiguous ranges)</span>
                    </div>
                    ${gapsHtml}
                </div>
            `;
        }

        odsGoalStateContainerEl.innerHTML = html;
    };

    // ===== O&D Reconciliation (stages + live activity) =====

    const STAGE_META = {
        'FETCH':        { color: '#fbbf24', label: 'Needs fetch', short: 'FETCH' },
        'CLASSIFY':     { color: '#f87171', label: 'Classifying', short: 'CLASSIFY' },
        'MATERIALIZE':  { color: '#f59e0b', label: 'Materializing', short: 'MAT' },
        'SATISFIED':    { color: '#34d399', label: 'Satisfied', short: 'MET' },
    };

    let lastReconData = null;

    const fetchOdsReconciliation = async () => {
        if (!odsGoalStateContainerEl) return;
        try {
            const res = await fetch('/api/ods/reconciliation');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            lastReconData = data;
            renderOdsReconciliation(data);
        } catch (err) {
            // non-fatal: the classic goal-state view still renders
            console.error('reconciliation fetch failed:', err);
        }
    };

    const renderOdsReconciliation = (data) => {
        if (!odsGoalStateContainerEl) return;
        const sets = Array.isArray(data.sets) ? data.sets : [];
        const act = data.activity || {};
        const pending = act.classification_pending || 0;
        const ingesting = act.ingesting || {};
        const lastCls = act.last_classifier ? new Date(act.last_classifier) : null;

        let html = '';

        // --- Live activity strip ---
        const activeChains = Object.entries(ingesting)
            .filter(([,v]) => v && v.active)
            .map(([k]) => k).sort().join(', ');
        html += `
            <div class="ods-activity-strip">
                <span class="summary-badge-pill" style="color:#fbbf24; background:#fbbf241a; border-color:#fbbf2433; font-size:0.72rem;">
                    ⏳ ${pending.toLocaleString()} awaiting classification
                </span>
                <span class="summary-badge-pill" style="color:${activeChains ? '#34d399' : '#f87171'}; background:${activeChains ? '#34d3991a' : '#f871711a'}; border-color:${activeChains ? '#34d39933' : '#f8717133'}; font-size:0.72rem;">
                    ${activeChains ? `● ingesting: ${activeChains}` : '○ no active ingestion'}
                </span>
                ${lastCls ? `<span class="dim-text" style="font-size:0.7rem;">classifier run ${timeAgo(lastCls)}</span>` : ''}
            </div>
        `;

        if (sets.length === 0) {
            html += `<div class="empty-state glass-card"><div class="empty-state-title">No O&amp;D sets declared</div><div class="empty-state-desc">Declare sets+products in config/ods-goal-state.yaml to see reconciliation stages.</div></div>`;
            odsGoalStateContainerEl.innerHTML = html;
            return;
        }

        // --- Per-set table: rows = requirement, columns = product ---
        const PRODUCT_ORDER = [
            'route.daily_stats', 'route.daily_stats_buckets',
            'pool.daily_stats', 'pool.daily_stats_buckets', 'pool.position_snapshots',
        ];

        sets.forEach(s => {
            s._prodMap = {};
            (s.products || []).forEach(p => { s._prodMap[p.product_id] = p; });
        });
        const usedProducts = [];
        PRODUCT_ORDER.forEach(pid => {
            if (sets.some(s => s._prodMap && s._prodMap[pid])) usedProducts.push(pid);
        });

        // Group products into Route / Pool families for a two-tier header.
        const GROUP_LABEL = { 'Route': 'route', 'Pool': 'pool' };
        const groupOf = (pid) => pid.startsWith('route') ? 'Route' : 'Pool';
        const routeProducts = usedProducts.filter(p => groupOf(p) === 'Route' && p !== 'route.swap_logs');
        const poolProducts = usedProducts.filter(p => groupOf(p) === 'Pool');

        html += `
            <table class="indexer-matrix-table ods-goal-state-table ods-recon-table">
                <thead>
                    <tr>
                        <th rowspan="2" style="text-align:left;">Requirement</th>
                        <th rowspan="2" style="text-align:left;">O&amp;D Set Part</th>
                        <th rowspan="2" style="text-align:left;">Chain</th>
                        <th rowspan="2" style="text-align:center;">Swap</th>
                        ${routeProducts.length ? `<th colspan="${routeProducts.length}" style="text-align:center;">Route</th>` : ''}
                        ${poolProducts.length ? `<th colspan="${poolProducts.length}" style="text-align:center;">Pool</th>` : ''}
                        <th rowspan="2" style="text-align:center;">Progress</th>
                    </tr>
                    <tr>
                        ${routeProducts.map(p => `<th style="text-align:center; font-size:0.66rem; white-space:nowrap;">${p.replace('route.', '')}</th>`).join('')}
                        ${poolProducts.map(p => `<th style="text-align:center; font-size:0.66rem; white-space:nowrap;">${p.replace('pool.', '')}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
        `;
        sets.forEach(s => {
            const st = STAGE_META[s.stage] || STAGE_META.FETCH;
            const progress = s.progress || 0;
            const g = { origin: s.origin, dest: s.dest, bidirectional: true, chains: s.chains };
            // Swap column: raw-swap coverage % of all required days for this set,
            // plus how much must still be fetched to leave the FETCH state.
            const cov = (s.swap_coverage_pct !== undefined) ? s.swap_coverage_pct : 100;
            const fetchPct = (s.swap_fetch_pct !== undefined) ? s.swap_fetch_pct : 0;
            const totalDays = (s.swap_total_days !== undefined) ? s.swap_total_days : 0;
            const covChip = fetchPct === 0
                ? `<span class="ods-stage-chip stage-met" title="all ${totalDays} required swap day(s) present">met ${cov}%</span>`
                : `<span class="ods-stage-chip stage-fetch" title="${totalDays} required swap day(s); ${fetchPct}% must still be fetched to leave FETCH">fetch ${fetchPct}%</span>`;
            html += `
                <tr>
                    <td style="text-align:left; font-weight:700; color:#f3f4f6;">
                        ${s.name}
                        <span class="summary-badge-pill" style="margin-left:8px; color:${st.color}; background:${st.color}1a; border-color:${st.color}33; font-size:0.62rem;">${st.short}</span>
                    </td>
                    <td style="text-align:left;">${odsSetPartHtml(g)}</td>
                    <td style="text-align:left;">${odsChainsHtml(g.chains)}</td>
                    <td style="text-align:center; white-space:nowrap;">${covChip}</td>
                    ${usedProducts.map(pid => {
                        const p = (s._prodMap || {})[pid];
                        if (!p) return `<td style="text-align:center;" class="dim-text">—</td>`;
                        const f = p.fetch || 0, c = p.classify || 0, m = p.materialize || 0;
                        const links = [];
                        if (f) links.push(`<span class="ods-stage-chip stage-fetch" title="raw missing: ${f} day(s)">fetch ${f}</span>`);
                        if (c) links.push(`<span class="ods-stage-chip stage-classify" title="raw present, unclassified: ${c} day(s)">classify ${c}</span>`);
                        if (m) links.push(`<span class="ods-stage-chip stage-mat" title="facts missing: ${m} day(s)">mat ${m}</span>`);
                        if (!f && !c && !m) links.push(`<span class="ods-stage-chip stage-met" title="all days satisfied">met ${p.resolved || 0}</span>`);
                        return `<td style="text-align:center; white-space:nowrap;">${links.join(' ')}<div style="font-size:0.62rem; color:#9ca3af;">${p.pct}%</div></td>`;
                    }).join('')}
                    <td style="text-align:center;">
                        <div class="health-meter-track" style="width:70px; height:5px;">
                            <div class="health-meter-fill ${progress >= 100 ? 'fill-green' : 'fill-yellow'}" style="width:${progress}%;"></div>
                        </div>
                    </td>
                </tr>
            `;
        });
        html += '</tbody></table>';

        odsGoalStateContainerEl.innerHTML = html;
    };

    const timeAgo = (d) => {
        const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
        if (s < 60) return `${s}s ago`;
        if (s < 3600) return `${Math.floor(s / 60)}m ago`;
        return `${Math.floor(s / 3600)}h ago`;
    };
    const renderSummaryMatrix = (tables) => {
        if (!summaryGridEl) return;
        const activeTables = ORDERED_TABLE_NAMES.filter(k => tables[k]);
        if (summaryBadgeCountEl) summaryBadgeCountEl.textContent = `${activeTables.length} Tables Tracked`;

        if (activeTables.length === 0) {
            summaryGridEl.innerHTML = `<div class="dim-text">No tables status available</div>`;
            return;
        }

        let html = '';
        activeTables.forEach(tName => {
            const tData = tables[tName];
            let isStale = false;
            let totalChecks = 0;
            let passChecks = 0;

            if (tData.checks) {
                const checkVals = Object.values(tData.checks);
                totalChecks = checkVals.length;
                checkVals.forEach(v => {
                    if (v === 'pass') passChecks++;
                    else if (v === 'fail') isStale = true;
                });
            } else if (tData.status === 'stale') {
                isStale = true;
            }

            const dotClass = isStale ? 'dot-amber' : 'dot-green';
            const badgeClass = isStale ? 'badge-stale' : 'badge-pass';
            const badgeLabel = isStale ? 'STALE' : (totalChecks > 0 ? `${passChecks}/${totalChecks} PASS` : 'OK');
            const cardClass = isStale ? 'card-stale' : 'card-fresh';
            const countStr = tData.count !== undefined ? formatNumber(tData.count) : '';

            html += `
                <div class="table-summary-card ${cardClass}" onclick="scrollToTableCard('${tName}')">
                    <div class="table-summary-main">
                        <span class="status-indicator-dot ${dotClass}"></span>
                        <div class="table-summary-info">
                            <span class="table-summary-name">${tName}</span>
                            <span class="table-summary-sub">${countStr ? countStr + ' rows' : 'Registry'}</span>
                        </div>
                    </div>
                    <span class="summary-badge-pill ${badgeClass}">${badgeLabel}</span>
                </div>
            `;
        });

        summaryGridEl.innerHTML = html;
    };

    window.toggleSectionGroup = (groupId) => {
        const headerEl = document.getElementById(`section-header-${groupId}`);
        const bodyEl = document.getElementById(`section-body-${groupId}`);
        if (headerEl && bodyEl) {
            const isCollapsed = bodyEl.classList.toggle('collapsed');
            headerEl.classList.toggle('collapsed', isCollapsed);
        }
    };

    window.scrollToTableCard = (tName) => {
        const el = document.getElementById(`table-detail-${tName}`);
        if (el) {
            const parentBody = el.closest('.theme-section-body');
            if (parentBody && parentBody.classList.contains('collapsed')) {
                const groupId = parentBody.id.replace('section-body-', '');
                toggleSectionGroup(groupId);
            }
            setTimeout(() => {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                el.style.borderColor = '#06b6d4';
                el.style.boxShadow = '0 0 24px rgba(6, 182, 212, 0.4)';
                setTimeout(() => {
                    el.style.borderColor = '';
                    el.style.boxShadow = '';
                }, 2000);
            }, 100);
        }
    };

    const renderChainsGrid = (swapsData) => {
        if (!chainsGridEl) return;
        if (!swapsData || !swapsData.chains) {
            chainsGridEl.innerHTML = `<div class="empty-state glass-card">No chain indexer metrics available</div>`;
            return;
        }

        const chains = swapsData.chains;
        let html = '';

        const chainKeys = Object.keys(chains).sort((a, b) => {
            if (a.toLowerCase() === 'ethereum') return -1;
            if (b.toLowerCase() === 'ethereum') return 1;
            return a.localeCompare(b);
        });

        chainKeys.forEach(chainName => {
            const chainObj = chains[chainName];
            const chainSlug = chainName.toLowerCase().replace(/\s+/g, '-');
            
            let isChainStale = false;
            if (chainObj.protocols) {
                Object.values(chainObj.protocols).forEach(p => {
                    if (p.checks && p.checks.is_fresher_than_3_hours === 'fail') {
                        isChainStale = true;
                    } else if (p.status === 'stale') {
                        isChainStale = true;
                    }
                });
            } else if (chainObj.status === 'stale') {
                isChainStale = true;
            }

            html += `
                <div class="chain-card glass-card chain-${chainSlug}">
                    <div class="chain-card-top">
                        <div class="chain-brand">
                            <span class="badge ${chainSlug}">${chainName}</span>
                            <span class="status-pill ${isChainStale ? 'pill-stale' : 'pill-fresh'}">
                                ${isChainStale ? 'STALE' : 'LIVE'}
                            </span>
                        </div>
                        <button class="btn-inspect-sm" onclick="openApiModal('/health/db/table/swaps/chains/${encodeURIComponent(chainName)}')">
                            Inspect JSON
                        </button>
                    </div>

                    <div class="protocols-stack">
            `;

            const protocols = chainObj.protocols || {};
            Object.keys(protocols).forEach(protoName => {
                const pObj = protocols[protoName];
                
                let isProtoStale = false;
                if (pObj.checks) {
                    isProtoStale = pObj.checks.is_fresher_than_3_hours === 'fail';
                } else if (pObj.status === 'stale') {
                    isProtoStale = true;
                }

                const timeAgoStr = formatTimeAgo(pObj.latest);

                html += `
                    <div class="proto-item">
                        <div class="proto-main font-mono">
                            <span class="proto-name">${protoName}</span>
                            <span class="proto-count">${formatNumber(pObj.count)} swaps</span>
                        </div>
                        <div class="proto-meta">
                            <span class="proto-date font-mono" title="${formatDate(pObj.latest)}">${timeAgoStr || formatDate(pObj.latest)}</span>
                            <span class="proto-badge ${isProtoStale ? 'stale' : 'fresh'}">
                                ${isProtoStale ? 'STALE' : 'FRESH'}
                            </span>
                        </div>
                    </div>
                `;
            });

            html += `
                    </div>
                </div>
            `;
        });

        chainsGridEl.innerHTML = html;
    };

    // LOWER SECTION: Detailed Table Inspector Cards Rendering (Thematic Groups)
    const renderTableDetailCards = (tablesObj) => {
        if (!tablesDetailContainerEl) return;
        const filterQuery = (tableSearchInput ? tableSearchInput.value : '').toLowerCase().trim();
        let html = '';

        SECTION_GROUPS.forEach(group => {
            const groupTables = group.tables.filter(tName => {
                if (!tablesObj[tName]) return false;
                if (filterQuery && !tName.toLowerCase().includes(filterQuery)) return false;
                return true;
            });

            if (groupTables.length === 0) return;

            html += `
                <div class="theme-section-header" onclick="toggleSectionGroup('${group.id}')" id="section-header-${group.id}">
                    <div class="theme-section-title">
                        <span class="theme-section-icon">${group.icon}</span>
                        <span>${group.title}</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span class="theme-section-badge">${groupTables.length} ${groupTables.length === 1 ? 'Table' : 'Tables'}</span>
                        <span class="chevron-toggle-icon">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="6 9 12 15 18 9"></polyline>
                            </svg>
                        </span>
                    </div>
                </div>
                <div class="theme-section-body" id="section-body-${group.id}">
            `;

            groupTables.forEach(tName => {
                const tData = tablesObj[tName];
                const meta = tableMetaMap[tName] || { 
                    title: tName, 
                    category: group.title, 
                    icon: group.icon,
                    defaultPolicy: 'System table metrics' 
                };

                let isStale = false;
                if (tData.checks) {
                    Object.values(tData.checks).forEach(v => {
                        if (v === 'fail') isStale = true;
                    });
                } else if (tData.status === 'stale') {
                    isStale = true;
                }

                const statusPillMarkup = !isStale 
                    ? `<span class="badge-status status-ok"><span class="dot-green"></span> Operational</span>` 
                    : `<span class="badge-status status-stale"><span class="dot-yellow"></span> SLA Degraded</span>`;

                let countDisplay = formatNumber(tData.count);
                if (tData.tracked_count !== undefined) {
                    countDisplay = `${formatNumber(tData.count)} <span class="dim-text">(${formatNumber(tData.tracked_count)} tracked)</span>`;
                }

                let policyStr = tData.freshness_requirement || meta.defaultPolicy;
                
                // Format SLA Checks
                let checksHtml = '';
                if (tData.checks) {
                    Object.keys(tData.checks).forEach(checkName => {
                        const res = tData.checks[checkName];
                        const isPass = res === 'pass';
                        checksHtml += `
                            <span class="check-pill-badge ${isPass ? 'pass' : 'fail'}">
                                ${isPass ? '✓' : '⚠'} ${checkName}: ${res.toUpperCase()}
                            </span>
                        `;
                    });
                }

                // Latest Date Formatting
                let rawLatest = tData.latest;
                if (typeof rawLatest === 'object' && rawLatest !== null) {
                    rawLatest = rawLatest.last_updated || rawLatest.symbol;
                }
                const latestFormatted = formatDate(rawLatest);
                const latestAgo = formatTimeAgo(rawLatest);

                // Earliest Date
                let rawEarliest = tData.earliest;
                const earliestFormatted = formatDate(rawEarliest);

                // Build Custom Deep Breakdown Panels
                let breakdownHtml = '';

                // 1. Swaps breakdown: Blockchain Indexer Freshness Matrix (Protocol rows × Chain columns)
                if (tName === 'swaps') {
                    const activeVolFilter = window.currentMatrixVolFilter || '0';
                    const matrixData = (tData.volume_filters && tData.volume_filters[activeVolFilter]) ? tData.volume_filters[activeVolFilter] : tData;

                    if (matrixData.chains) {
                        const chainNames = Object.keys(matrixData.chains).sort((a, b) => {
                            if (a.toLowerCase() === 'ethereum') return -1;
                            if (b.toLowerCase() === 'ethereum') return 1;
                            return a.localeCompare(b);
                        });

                        // Gather distinct protocol names across all chains
                        const protoSet = new Set();
                        chainNames.forEach(cName => {
                            const protos = matrixData.chains[cName].protocols || {};
                            Object.keys(protos).forEach(p => protoSet.add(p));
                        });

                        const getProtoPriority = (p) => {
                            const name = p.toLowerCase();
                            if (name.includes('uniswap v2')) return 1;
                            if (name.includes('uniswap v3')) return 2;
                            if (name.includes('uniswap v4')) return 3;
                            if (name.includes('uniswap')) return 4;
                            if (name.includes('pancakeswap v3')) return 10;
                            if (name.includes('pancakeswap v4')) return 11;
                            if (name.includes('pancakeswap')) return 12;
                            if (name.includes('aerodrome')) return 20;
                            return 99;
                        };

                        const protoList = Array.from(protoSet).sort((a, b) => {
                            const prioA = getProtoPriority(a);
                            const prioB = getProtoPriority(b);
                            if (prioA !== prioB) return prioA - prioB;
                            return a.localeCompare(b);
                        });

                        const getVolBtnStyle = (val) => {
                            const isActive = (activeVolFilter === val);
                            return `padding:4px 10px; font-size:0.75rem; font-weight:600; border:none; border-radius:6px; cursor:pointer; transition:all 0.2s; ${isActive ? 'background:#6366f1; color:#ffffff; box-shadow:0 2px 4px rgba(99,102,241,0.4);' : 'background:transparent; color:#94a3b8;'}`;
                        };

                        breakdownHtml += `
                            <div class="breakdown-subpanel">
                                <div class="subpanel-title" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
                                    <div style="display:flex; align-items:center; gap:8px;">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
                                        <span>Swap Staleness Matrix</span>
                                    </div>
                                    <div class="matrix-filter-group" style="display:inline-flex; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:2px;">
                                        <button onclick="setMatrixVolumeFilter('0')" style="${getVolBtnStyle('0')}">All Pools</button>
                                        <button onclick="setMatrixVolumeFilter('1000')" style="${getVolBtnStyle('1000')}">&gt; $1k (7d Vol)</button>
                                        <button onclick="setMatrixVolumeFilter('100000')" style="${getVolBtnStyle('100000')}">&gt; $100k (7d Vol)</button>
                                        <button onclick="setMatrixVolumeFilter('10000000')" style="${getVolBtnStyle('10000000')}">&gt; $10M (7d Vol)</button>
                                    </div>
                                </div>
                                <div style="overflow-x: auto;">
                                    <table class="indexer-matrix-table">
                                        <thead>
                                            <tr>
                                                <th style="text-align: left;">DEX Protocol</th>
                                                ${chainNames.map(c => `<th style="text-align: center;">${c}</th>`).join('')}
                                                <th style="text-align: right;">Total Swaps</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                        `;

                    // Track per-chain aggregates for the summary footer
                    const chainSummary = {};
                    chainNames.forEach(cName => {
                        chainSummary[cName] = { totalCount: 0, latestMs: null, commonEarliestMs: null, anyStale: false, anyContinuityFail: false };
                    });

                    protoList.forEach(protoName => {
                        let protoTotal = 0;
                        let rowCellsHtml = '';

                        chainNames.forEach(cName => {
                            const pData = (matrixData.chains[cName].protocols || {})[protoName];
                            if (pData) {
                                protoTotal += (pData.count || 0);
                                chainSummary[cName].totalCount += (pData.count || 0);

                                // Common window right bound = earliest-of-latests (the most stale protocol's last update)
                                const latMs = pData.latest ? new Date(pData.latest).getTime() : null;
                                if (latMs && (!chainSummary[cName].latestMs || latMs < chainSummary[cName].latestMs)) {
                                    chainSummary[cName].latestMs = latMs;
                                }
                                // Common window left bound = latest-of-earliests (most recent protocol start)
                                const earMs = pData.earliest ? new Date(pData.earliest).getTime() : null;
                                if (earMs && (!chainSummary[cName].commonEarliestMs || earMs > chainSummary[cName].commonEarliestMs)) {
                                    chainSummary[cName].commonEarliestMs = earMs;
                                }

                                const isFresh = pData.checks && pData.checks.is_fresher_than_3_hours === 'pass';
                                if (!isFresh) chainSummary[cName].anyStale = true;
                                if (pData.checks && pData.checks.has_data_every_day === 'fail') chainSummary[cName].anyContinuityFail = true;
                                const hasEveryDay = pData.checks && pData.checks.has_data_every_day === 'pass';
                                // grey = ok, yellow = continuity problem
                                const arrowColor = hasEveryDay ? '#6b7280' : '#f59e0b';
                                const tooltipText = hasEveryDay ? 'Daily continuity check passed' : 'Daily continuity check failed';

                                const earliestShort = formatShortTimeAgo(pData.earliest);
                                const latestShort = formatShortTimeAgo(pData.latest);

                                rowCellsHtml += `
                                    <td style="text-align: center;">
                                        <div class="matrix-cell">
                                            <div style="display:flex; align-items:center; justify-content:center; gap:4px; font-size:0.85rem; font-weight:600;" class="font-mono">
                                                <span class="status-indicator-dot ${isFresh ? 'dot-green' : 'dot-amber'}" style="flex-shrink:0;"></span>
                                                <span class="dim-text">${earliestShort}</span>
                                                <span style="color:${arrowColor}; cursor:help;" title="${tooltipText}">➔</span>
                                                <span style="${isFresh ? 'color:#6b7280;' : 'color:#f9fafb; font-weight:700;'}">${latestShort}</span>
                                            </div>
                                            <div style="font-size:0.72rem; color:#6b7280;" class="font-mono">
                                                ${formatNumber(pData.count)}
                                            </div>
                                        </div>
                                    </td>
                                `;
                            } else {
                                rowCellsHtml += `<td style="text-align: center;"></td>`;
                            }
                        });

                        breakdownHtml += `
                            <tr>
                                <td class="font-bold font-mono" style="color:#60a5fa; text-align: left;">${protoName}</td>
                                ${rowCellsHtml}
                                <td class="font-mono font-bold" style="text-align: right; color:#34d399;">${formatNumber(protoTotal)}</td>
                            </tr>
                        `;
                    });

                    // Chain summary footer
                    let footerCellsHtml = '';
                    chainNames.forEach(cName => {
                        const cs = chainSummary[cName];
                        if (cs.totalCount > 0) {
                            const latestShort = cs.latestMs ? formatShortTimeAgo(new Date(cs.latestMs).toISOString()) : '';
                            const commonEarliestShort = cs.commonEarliestMs ? formatShortTimeAgo(new Date(cs.commonEarliestMs).toISOString()) : '';
                            const hasIssue = cs.anyStale || cs.anyContinuityFail;
                            const dotClass = hasIssue ? 'dot-amber' : 'dot-green';
                            // Bright white when stale (>3h), dim grey otherwise
                            const latestStyle = cs.anyStale ? 'color:#f9fafb; font-weight:700;' : 'color:#6b7280;';
                            footerCellsHtml += `
                                <td style="text-align: center; border-top: 2px solid rgba(99,102,241,0.75); padding-top: 10px;">
                                    <div class="matrix-cell">
                                        <div style="display:flex; align-items:center; justify-content:center; gap:4px; font-size:0.85rem; font-weight:600;" class="font-mono">
                                            <span class="status-indicator-dot ${dotClass}" style="flex-shrink:0;"></span>
                                            <span class="dim-text">${commonEarliestShort}</span>
                                            <span style="color:#818cf8; margin:0 2px;">➔</span>
                                            <span style="${latestStyle}">${latestShort}</span>
                                        </div>
                                        <div style="font-size:0.72rem; color:#6b7280;" class="font-mono">${formatNumber(cs.totalCount)}</div>
                                    </div>
                                </td>
                            `;
                        } else {
                            footerCellsHtml += `<td style="border-top: 2px solid rgba(99,102,241,0.75);"></td>`;
                        }
                    });

                    const grandTotal = chainNames.reduce((s, c) => s + chainSummary[c].totalCount, 0);

                    breakdownHtml += `
                            <tr>
                                <td class="font-bold" style="text-align: left; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">
                                    Σ Chain Total
                                </td>
                                ${footerCellsHtml}
                                <td class="font-mono font-bold" style="text-align: right; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">${formatNumber(grandTotal)}</td>
                            </tr>
                                    </tbody>
                                </table>
                        </div>
                    `;
                    }
                }

                // 2. Coin contract coverage breakdown
                if (tName === 'coin' && tData.contract_coverage) {
                    const cov = tData.contract_coverage;
                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title">Multi-Chain Token Contract Mapping Coverage</div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Overall (Any Chain)</span><span class="font-bold text-success">${cov.any_chain_percentage}%</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cov.any_chain_percentage}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Ethereum</span><span class="font-bold">${cov.ethereum_percentage}%</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cov.ethereum_percentage}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">BNB Chain</span><span class="font-bold">${cov.bnb_percentage}%</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cov.bnb_percentage}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Base</span><span class="font-bold">${cov.base_percentage}%</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cov.base_percentage}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Arbitrum</span><span class="font-bold">${cov.arbitrum_percentage}%</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cov.arbitrum_percentage}%;"></div></div>
                                </div>
                            </div>
                        </div>
                    `;
                }

                // 3. Coin contract breakdown by chain
                if (tName === 'coin_contract' && tData.chains) {
                    breakdownHtml += `<div class="breakdown-subpanel"><div class="subpanel-title">Contract Address Distribution per Network</div><div style="display: flex; flex-wrap: wrap; gap: 10px;">`;
                    Object.keys(tData.chains).forEach(cName => {
                        const cObj = tData.chains[cName];
                        breakdownHtml += `<div class="proto-item font-mono" style="padding: 6px 12px;"><span class="font-bold text-success">${cName}:</span> <span>${formatNumber(cObj.count)} addresses</span></div>`;
                    });
                    breakdownHtml += `</div></div>`;
                }

                // 4. Liquidity pool breakdown by chain with history coverage matrix
                if (tName === 'liquidity_pool') {
                    const activeVolFilter = window.currentMatrixVolFilter || '0';
                    const matrixData = (tData.volume_filters && tData.volume_filters[activeVolFilter]) ? tData.volume_filters[activeVolFilter] : tData;

                    if (matrixData.chains) {
                        const chainNames = Object.keys(matrixData.chains).sort((a, b) => {
                            if (a.toLowerCase() === 'ethereum') return -1;
                            if (b.toLowerCase() === 'ethereum') return 1;
                            return a.localeCompare(b);
                        });

                        // Gather distinct protocol names across all chains
                        const protoSet = new Set();
                        chainNames.forEach(cName => {
                            const protos = matrixData.chains[cName].protocols || {};
                            Object.keys(protos).forEach(p => protoSet.add(p));
                        });

                        const getProtoPriority = (p) => {
                            const name = p.toLowerCase();
                            if (name.includes('uniswap v2')) return 1;
                            if (name.includes('uniswap v3')) return 2;
                            if (name.includes('uniswap v4')) return 3;
                            if (name.includes('uniswap')) return 4;
                            if (name.includes('pancakeswap v3')) return 10;
                            if (name.includes('pancakeswap v4')) return 11;
                            if (name.includes('pancakeswap')) return 12;
                            if (name.includes('aerodrome')) return 20;
                            return 99;
                        };

                        const protoList = Array.from(protoSet).sort((a, b) => {
                            const prioA = getProtoPriority(a);
                            const prioB = getProtoPriority(b);
                            if (prioA !== prioB) return prioA - prioB;
                            return a.localeCompare(b);
                        });

                        const getVolBtnStyle = (val) => {
                            const isActive = (activeVolFilter === val);
                            return `padding:4px 10px; font-size:0.75rem; font-weight:600; border:none; border-radius:6px; cursor:pointer; transition:all 0.2s; ${isActive ? 'background:#6366f1; color:#ffffff; box-shadow:0 2px 4px rgba(99,102,241,0.4);' : 'background:transparent; color:#94a3b8;'}`;
                        };

                        breakdownHtml += `
                            <div class="breakdown-subpanel">
                                <div class="subpanel-title" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
                                    <div style="display:flex; align-items:center; gap:8px;">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>
                                        <span>Pool Count & History Coverage Matrix</span>
                                    </div>
                                    <div class="matrix-filter-group" style="display:inline-flex; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:2px;">
                                        <button onclick="setMatrixVolumeFilter('0')" style="${getVolBtnStyle('0')}">All Pools</button>
                                        <button onclick="setMatrixVolumeFilter('1000')" style="${getVolBtnStyle('1000')}">&gt; $1k (7d Vol)</button>
                                        <button onclick="setMatrixVolumeFilter('100000')" style="${getVolBtnStyle('100000')}">&gt; $100k (7d Vol)</button>
                                        <button onclick="setMatrixVolumeFilter('10000000')" style="${getVolBtnStyle('10000000')}">&gt; $10M (7d Vol)</button>
                                    </div>
                                </div>
                                <div style="overflow-x: auto;">
                                    <table class="indexer-matrix-table">
                                        <thead>
                                            <tr>
                                                <th style="text-align: left;">DEX Protocol</th>
                                                ${chainNames.map(c => `<th style="text-align: center;">${c}</th>`).join('')}
                                                <th style="text-align: right;">Total Pools</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                        `;

                        const chainSummary = {};
                        chainNames.forEach(cName => {
                            chainSummary[cName] = { totalPools: 0, totalCovered: 0, totalFresh: 0, coverageSum: 0, protoCount: 0 };
                        });

                        protoList.forEach(protoName => {
                            let protoTotal = 0;
                            let protoCovered = 0;
                            let protoFresh = 0;
                            let rowCellsHtml = '';

                            chainNames.forEach(cName => {
                                const pData = (matrixData.chains[cName].protocols || {})[protoName];
                                if (pData && pData.count > 0) {
                                    protoTotal += (pData.count || 0);
                                    protoCovered += (pData.covered_count || 0);
                                    protoFresh += (pData.fresh_count || 0);
                                    chainSummary[cName].totalPools += (pData.count || 0);
                                    chainSummary[cName].totalCovered += (pData.covered_count || 0);
                                    chainSummary[cName].totalFresh += (pData.fresh_count || 0);
                                    chainSummary[cName].protoCount++;

                                    const covPct = pData.coverage_percentage || 0;
                                    const tvlCovPct = pData.tvl_coverage_percentage || 0;
                                    const freshPct = pData.fresh_percentage || 0;
                                    const isFullyCovered = covPct >= 90;
                                    const isPartiallyCovered = covPct >= 50;
                                    const isFresh = freshPct >= 50;

                                    const covColor = isFullyCovered ? '#34d399' : (isPartiallyCovered ? '#fbbf24' : '#ef4444');
                                    const freshDot = isFresh ? 'dot-green' : 'dot-amber';

                                    rowCellsHtml += `
                                        <td style="text-align: center;">
                                            <div class="matrix-cell">
                                                <div style="display:flex; align-items:center; justify-content:center; gap:4px; font-size:0.85rem; font-weight:600;" class="font-mono">
                                                    <span class="status-indicator-dot ${freshDot}" style="flex-shrink:0;"></span>
                                                    <span style="color:#f9fafb;">${formatNumber(pData.count)}</span>
                                                    <span class="dim-text">pools</span>
                                                </div>
                                                <div style="font-size:0.72rem; color:#6b7280;" class="font-mono">
                                                    <span style="color:${covColor};" title="History & TVL Coverage">${covPct}% hist</span>
                                                    <span class="dim-text"> | </span>
                                                    <span style="${freshPct >= 50 ? 'color:#34d399;' : 'color:#fbbf24;'}">${freshPct}% fresh</span>
                                                </div>
                                            </div>
                                        </td>
                                    `;
                                } else {
                                    rowCellsHtml += `<td style="text-align: center;"></td>`;
                                }
                            });

                        breakdownHtml += `
                            <tr>
                                <td class="font-bold font-mono" style="color:#60a5fa; text-align: left;">${protoName}</td>
                                ${rowCellsHtml}
                                <td class="font-mono font-bold" style="text-align: right;">
                                    <span style="color:#34d399;">${formatNumber(protoTotal)}</span>
                                    <span class="dim-text" style="font-size:0.75rem;"> pools</span>
                                </td>
                            </tr>
                        `;
                    });

                    // Chain summary footer
                    let footerCellsHtml = '';
                    chainNames.forEach(cName => {
                        const cs = chainSummary[cName];
                        if (cs.totalPools > 0) {
                            const aggCovPct = Math.round((cs.totalCovered / cs.totalPools) * 100);
                            const aggFreshPct = Math.round((cs.totalFresh / cs.totalPools) * 100);
                            const hasIssue = aggCovPct < 90 || aggFreshPct < 50;
                            const dotClass = hasIssue ? 'dot-amber' : 'dot-green';

                            footerCellsHtml += `
                                <td style="text-align: center; border-top: 2px solid rgba(99,102,241,0.75); padding-top: 10px;">
                                    <div class="matrix-cell">
                                        <div style="display:flex; align-items:center; justify-content:center; gap:4px; font-size:0.85rem; font-weight:600;" class="font-mono">
                                            <span class="status-indicator-dot ${dotClass}" style="flex-shrink:0;"></span>
                                            <span style="color:#f9fafb;">${formatNumber(cs.totalPools)}</span>
                                            <span class="dim-text">pools</span>
                                        </div>
                                        <div style="font-size:0.72rem; color:#6b7280;" class="font-mono">
                                            <span style="color:#a78bfa;">${aggCovPct}% hist</span>
                                            <span style="color:#6b7280;"> | </span>
                                            <span style="${aggFreshPct >= 50 ? 'color:#34d399;' : 'color:#fbbf24;'}">${aggFreshPct}% fresh</span>
                                        </div>
                                    </div>
                                </td>
                            `;
                        } else {
                            footerCellsHtml += `<td style="border-top: 2px solid rgba(99,102,241,0.75);"></td>`;
                        }
                    });

                    const grandTotal = chainNames.reduce((s, c) => s + chainSummary[c].totalPools, 0);

                    breakdownHtml += `
                            <tr>
                                <td class="font-bold" style="text-align: left; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">
                                    Σ Chain Total
                                </td>
                                ${footerCellsHtml}
                                <td class="font-mono font-bold" style="text-align: right; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">${formatNumber(grandTotal)}</td>
                            </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    `;
                    }

                    // 4b. Liquidity pool history coverage matrix (pool-level passing metric)
                    if (tablesObj.liquidity_pool_daily_stats && tablesObj.liquidity_pool_daily_stats.chains) {
                        const activeVolFilter = window.currentMatrixVolFilter || '0';
                        const lphVolData = (tablesObj.liquidity_pool_daily_stats.volume_filters && tablesObj.liquidity_pool_daily_stats.volume_filters[activeVolFilter])
                            ? tablesObj.liquidity_pool_daily_stats.volume_filters[activeVolFilter]
                            : tablesObj.liquidity_pool_daily_stats;
                        const lphChains = lphVolData.chains || tablesObj.liquidity_pool_daily_stats.chains;
                        const lookbackDays = tablesObj.liquidity_pool_daily_stats.lookback_days || 7;
                        const lphChainNames = Object.keys(lphChains).sort((a, b) => {
                            if (a.toLowerCase() === 'ethereum') return -1;
                            if (b.toLowerCase() === 'ethereum') return 1;
                            return a.localeCompare(b);
                        });

                        // Build proto list from unfiltered data so protocols don't disappear at higher thresholds
                        const lphAllChains = tablesObj.liquidity_pool_daily_stats.chains;
                        const lphProtoSet = new Set();
                        Object.keys(lphAllChains).forEach(cName => {
                            const protos = lphAllChains[cName].protocols || {};
                            Object.keys(protos).forEach(p => lphProtoSet.add(p));
                        });
                        // Also include volume-filtered protocols if not already present
                        lphChainNames.forEach(cName => {
                            const protos = lphChains[cName].protocols || {};
                            Object.keys(protos).forEach(p => lphProtoSet.add(p));
                        });

                        const getLphProtoPriority = (p) => {
                            const name = p.toLowerCase();
                            if (name.includes('uniswap v2')) return 1;
                            if (name.includes('uniswap v3')) return 2;
                            if (name.includes('uniswap v4')) return 3;
                            if (name.includes('uniswap')) return 4;
                            if (name.includes('pancakeswap v3')) return 10;
                            if (name.includes('pancakeswap v4')) return 11;
                            if (name.includes('pancakeswap')) return 12;
                            if (name.includes('aerodrome')) return 20;
                            return 99;
                        };

                        const lphProtoList = Array.from(lphProtoSet).sort((a, b) => {
                            const prioA = getLphProtoPriority(a);
                            const prioB = getLphProtoPriority(b);
                            if (prioA !== prioB) return prioA - prioB;
                            return a.localeCompare(b);
                        });

                        const getLphLookbackBtnStyle = (val) => {
                            const isActive = (lookbackDays === val);
                            return `padding:4px 10px; font-size:0.75rem; font-weight:600; border:none; border-radius:6px; cursor:pointer; transition:all 0.2s; ${isActive ? 'background:#6366f1; color:#ffffff; box-shadow:0 2px 4px rgba(99,102,241,0.4);' : 'background:transparent; color:#94a3b8;'}`;
                        };

                        const getLphVolBtnStyle = (val) => {
                            const isActive = (activeVolFilter === val);
                            return `padding:4px 10px; font-size:0.75rem; font-weight:600; border:none; border-radius:6px; cursor:pointer; transition:all 0.2s; ${isActive ? 'background:#6366f1; color:#ffffff; box-shadow:0 2px 4px rgba(99,102,241,0.4);' : 'background:transparent; color:#94a3b8;'}`;
                        };

                        window.setLphLookback = function(val) {
                            fetch('/health?lph_lookback=' + val)
                                .then(r => r.json())
                                .then(data => {
                                    if (data.db && data.db.table) {
                                        const t = data.db.table;
                                        if (currentHealthData) {
                                            currentHealthData.db.table = t;
                                            renderHealthUI(currentHealthData);
                                        }
                                    }
                                });
                        };

                        const volBtnHtml = `
                            <div class="matrix-filter-group" style="display:inline-flex; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:2px;">
                                <button onclick="setMatrixVolumeFilter('0')" style="${getLphVolBtnStyle('0')}">All Pools</button>
                                <button onclick="setMatrixVolumeFilter('1000')" style="${getLphVolBtnStyle('1000')}">&gt; $1k</button>
                                <button onclick="setMatrixVolumeFilter('100000')" style="${getLphVolBtnStyle('100000')}">&gt; $100k</button>
                                <button onclick="setMatrixVolumeFilter('10000000')" style="${getLphVolBtnStyle('10000000')}">&gt; $10M</button>
                            </div>
                            <div class="matrix-filter-group" style="display:inline-flex; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:2px; margin-left:6px;">
                                <button onclick="setLphLookback(1)" style="${getLphLookbackBtnStyle(1)}">1d</button>
                                <button onclick="setLphLookback(3)" style="${getLphLookbackBtnStyle(3)}">3d</button>
                                <button onclick="setLphLookback(7)" style="${getLphLookbackBtnStyle(7)}">7d</button>
                                <button onclick="setLphLookback(14)" style="${getLphLookbackBtnStyle(14)}">14d</button>
                                <button onclick="setLphLookback(30)" style="${getLphLookbackBtnStyle(30)}">30d</button>
                                <button onclick="setLphLookback(90)" style="${getLphLookbackBtnStyle(90)}">90d</button>
                            </div>
                        `;

                        breakdownHtml += `
                            <div class="breakdown-subpanel">
                                <div class="subpanel-title" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
                                    <div style="display:flex; align-items:center; gap:8px;">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                                        <span>Pool History Coverage Matrix (TVL &gt; 0, last ${lookbackDays}d)</span>
                                    </div>
                                    <div style="display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
                                        ${volBtnHtml}
                                    </div>
                                </div>
                                <div style="overflow-x: auto;">
                                    <table class="indexer-matrix-table">
                                        <thead>
                                            <tr>
                                                <th style="text-align: left;">DEX Protocol</th>
                                                ${lphChainNames.map(c => `<th style="text-align: center;">${c}</th>`).join('')}
                                                <th style="text-align: right;">Pools Passing</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                        `;

                        const lphChainSummary = {};
                        lphChainNames.forEach(cName => {
                            lphChainSummary[cName] = { totalPools: 0, passingPools: 0 };
                        });

                        lphProtoList.forEach(protoName => {
                            let protoTotal = 0;
                            let protoPassing = 0;
                            let rowCellsHtml = '';

                            lphChainNames.forEach(cName => {
                                const protosExistOnChain = (lphAllChains[cName] && lphAllChains[cName].protocols && lphAllChains[cName].protocols[protoName]);
                                const pData = (lphChains[cName].protocols || {})[protoName];
                                if (protosExistOnChain) {
                                    if (pData) {
                                        protoTotal += (pData.total_pools || 0);
                                        protoPassing += (pData.passing_pools || 0);
                                        lphChainSummary[cName].totalPools += (pData.total_pools || 0);
                                        lphChainSummary[cName].passingPools += (pData.passing_pools || 0);

                                        const passing = pData.passing_pools || 0;
                                        const total = pData.total_pools || 0;
                                        const pct = pData.passing_pct || 0;
                                        const pctColor = pct >= 80 ? '#34d399' : (pct >= 50 ? '#fbbf24' : '#ef4444');

                                        rowCellsHtml += `
                                            <td style="text-align: center;">
                                                <div class="matrix-cell">
                                                    <div style="font-size:0.9rem; font-weight:700;" class="font-mono">
                                                        <span style="color:${pctColor};">${pct}%</span>
                                                    </div>
                                                    <div style="font-size:0.7rem; color:#6b7280;" class="font-mono">
                                                        <span>${formatNumber(passing)}</span>
                                                        <span class="dim-text"> / ${formatNumber(total)} pools</span>
                                                    </div>
                                                </div>
                                            </td>
                                        `;
                                    } else {
                                        rowCellsHtml += `
                                            <td style="text-align: center;">
                                                <div class="matrix-cell">
                                                    <div style="font-size:0.9rem; font-weight:700;" class="font-mono">
                                                        <span style="color:#6b7280;">—%</span>
                                                    </div>
                                                    <div style="font-size:0.7rem; color:#6b7280;" class="font-mono">
                                                        <span>0</span>
                                                        <span class="dim-text"> / 0 pools</span>
                                                    </div>
                                                </div>
                                            </td>
                                        `;
                                    }
                                } else {
                                    rowCellsHtml += `<td style="text-align: center;"></td>`;
                                }
                            });

                            const protoPct = protoTotal > 0 ? Math.round(protoPassing / protoTotal * 100) : 0;
                            breakdownHtml += `
                                <tr>
                                    <td class="font-bold font-mono" style="color:#60a5fa; text-align: left;">${protoName}</td>
                                    ${rowCellsHtml}
                                    <td class="font-mono font-bold" style="text-align: right;">
                                        <span style="color:#34d399;">${formatNumber(protoPassing)}</span>
                                        <span class="dim-text"> / ${formatNumber(protoTotal)}</span>
                                    </td>
                                </tr>
                            `;
                        });

                        let footerCellsHtml = '';
                        lphChainNames.forEach(cName => {
                            const cs = lphChainSummary[cName];
                            if (cs.totalPools > 0) {
                                const aggPct = Math.round(cs.passingPools / cs.totalPools * 100);
                                footerCellsHtml += `
                                    <td style="text-align: center; border-top: 2px solid rgba(99,102,241,0.75); padding-top: 10px;">
                                        <div class="matrix-cell">
                                            <div style="font-size:0.82rem; color:#a78bfa;" class="font-mono">
                                                <span style="font-weight:600;">${formatNumber(cs.passingPools)}</span>
                                                <span class="dim-text"> / ${formatNumber(cs.totalPools)} pools</span>
                                                <br>
                                                <span style="font-weight:600; font-size:0.72rem;">${aggPct}%</span>
                                            </div>
                                        </div>
                                    </td>
                                `;
                            } else {
                                footerCellsHtml += `<td style="border-top: 2px solid rgba(99,102,241,0.75);"></td>`;
                            }
                        });

                        const lphGrandTotal = lphChainNames.reduce((s, c) => s + lphChainSummary[c].totalPools, 0);
                        const lphGrandPassing = lphChainNames.reduce((s, c) => s + lphChainSummary[c].passingPools, 0);

                        breakdownHtml += `
                                <tr>
                                    <td class="font-bold" style="text-align: left; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">
                                        Σ Chain Total
                                    </td>
                                    ${footerCellsHtml}
                                    <td class="font-mono font-bold" style="text-align: right; border-top: 2px solid rgba(99,102,241,0.75); color:#a78bfa; padding-top: 10px;">
                                        <span style="color:#a78bfa;">${formatNumber(lphGrandPassing)}</span>
                                        <span class="dim-text"> / ${formatNumber(lphGrandTotal)}</span>
                                    </td>
                                </tr>
                                        </tbody>
                                    </table>
                                </div>
                                <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:10px; padding:8px 12px; background:rgba(15,23,42,0.6); border:1px solid rgba(255,255,255,0.08); border-radius:6px; font-size:0.75rem;">
                                    <span style="color:#94a3b8; font-weight:600;">Legend:</span>
                                    <span style="color:#e2e8f0;">🟢 Passing</span>
                                    <span style="color:#e2e8f0;">🟡 Partial</span>
                                    <span style="color:#e2e8f0;">🔴 Failing</span>
                                    <span style="color:#6b7280;">|</span>
                                    <span style="color:#94a3b8;">A pool <em>passes</em> if every day in the lookback window has TVL &gt; 0. Dormant pools (0 tx, 0 vol) with healthy TVL are considered passing.</span>
                                </div>
                            </div>
                        `;
                    }
                }

                // 5. Coin price history coverage breakdown
                if (tName === 'swaps' && tData.route_assignment) {
                    const assignment = tData.route_assignment;
                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title">Route Assignment Coverage</div>
                            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;">
                                        <span class="dim-text">Assigned to a route</span>
                                        <span class="font-bold text-success">${formatNumber(assignment.assigned_count)} (${assignment.assigned_percentage}%)</span>
                                    </div>
                                    <div class="health-meter-track"><div class="health-meter-fill ${assignment.assigned_percentage >= 99 ? 'fill-green' : 'fill-yellow'}" style="width:${Math.min(100, assignment.assigned_percentage)}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;">
                                        <span class="dim-text">Not assigned</span>
                                        <span class="font-bold ${assignment.unassigned_count > 0 ? 'text-warning' : 'text-success'}">${formatNumber(assignment.unassigned_count)}</span>
                                    </div>
                                    <div class="health-meter-track"><div class="health-meter-fill ${assignment.unassigned_count > 0 ? 'fill-yellow' : 'fill-green'}" style="width:${assignment.total_count ? Math.min(100, assignment.unassigned_count / assignment.total_count * 100) : 0}%;"></div></div>
                                </div>
                            </div>
                            <div class="dim-text" style="font-size:0.75rem; margin-top:10px;">${formatNumber(assignment.assigned_count)} of ${formatNumber(assignment.total_count)} swap log entries have a route assignment.</div>
                        </div>
                    `;
                }

                // 6. Coin price history coverage breakdown
                if (tName === 'coin_price_history' && tData.covered_coins) {
                    const cc = tData.covered_coins;
                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title">Coin Price History Coverage & Freshness</div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">History Coverage (Coins)</span><span class="font-bold text-success">${formatNumber(cc.count)} coins (${cc.percentage}%)</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${Math.min(100, cc.percentage * 5)}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Fresh Data (>= Yesterday)</span><span class="font-bold ${cc.fresh_count > 0 ? 'text-success' : 'text-warning'}">${formatNumber(cc.fresh_count)} coins (${cc.fresh_percentage}%)</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${Math.min(100, cc.fresh_percentage * 5)}%;"></div></div>
                                </div>
                            </div>
                        </div>
                    `;
                }

                // 6. Liquidity pool history coverage breakdown
                if (tName === 'liquidity_pool_daily_stats' && tablesObj.liquidity_pool) {
                    const activeVolFilter = window.currentMatrixVolFilter || '0';
                    const matrixData = (tablesObj.liquidity_pool.volume_filters && tablesObj.liquidity_pool.volume_filters[activeVolFilter]) ? tablesObj.liquidity_pool.volume_filters[activeVolFilter] : (tData.covered_pools ? tData : tablesObj.liquidity_pool);
                    const cp = matrixData.covered_pools || tData.covered_pools;
                    if (cp) {
                        const formatVolLabel = (v) => {
                            if (v === '0') return 'All Pools';
                            if (v === '1000') return '> $1k';
                            if (v === '100000') return '> $100k';
                            if (v === '10000000') return '> $10M';
                            return `> $${v}`;
                        };
                        const filterLabel = formatVolLabel(activeVolFilter);
                        breakdownHtml += `
                            <div class="breakdown-subpanel">
                                <div class="subpanel-title">Pool History Metrics Coverage & Freshness (${filterLabel})</div>
                                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
                                    <div>
                                        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">History Coverage (Pools)</span><span class="font-bold text-success">${formatNumber(cp.count)} pools (${cp.percentage}%)</span></div>
                                        <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${cp.percentage}%;"></div></div>
                                    </div>
                                    <div>
                                        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Fresh Data (>= Yesterday)</span><span class="font-bold ${cp.fresh_count > 0 ? 'text-success' : 'text-warning'}">${formatNumber(cp.fresh_count)} pools (${cp.fresh_percentage}%)</span></div>
                                        <div class="health-meter-track"><div class="health-meter-fill ${cp.fresh_percentage > 50 ? 'fill-green' : 'fill-yellow'}" style="width: ${cp.fresh_percentage}%;"></div></div>
                                    </div>
                                </div>
                            </div>
                        `;
                    }
                }

                // 7. Position snapshot coverage breakdown
                if ((tName === 'liquidity_pool_position' || tName === 'liquidity_pool_position_snapshot') && (tData.snapshot_coverage || tData.covered_positions)) {
                    const cp = tData.snapshot_coverage || tData.covered_positions;
                    const posCount = cp.covered_positions_count !== undefined ? cp.covered_positions_count : cp.count;
                    const posPct = cp.covered_positions_percentage !== undefined ? cp.covered_positions_percentage : cp.percentage;
                    const freshCount = cp.fresh_positions_count !== undefined ? cp.fresh_positions_count : cp.fresh_count;
                    const freshPct = cp.fresh_positions_percentage !== undefined ? cp.fresh_positions_percentage : cp.fresh_percentage;
                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title">LP Position Snapshots Coverage & Freshness</div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Snapshot Coverage</span><span class="font-bold text-success">${formatNumber(posCount)} positions (${posPct}%)</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${posPct}%;"></div></div>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;"><span class="dim-text">Fresh Data (>= Yesterday)</span><span class="font-bold text-success">${formatNumber(freshCount)} positions (${freshPct}%)</span></div>
                                    <div class="health-meter-track"><div class="health-meter-fill fill-green" style="width: ${freshPct}%;"></div></div>
                                </div>
                            </div>
                        </div>
                    `;
                }

                // Route taxonomy chain breakdown
                if (tName === 'route_taxonomy' && tData.chains) {
                    _taxonomyData = tData;
                    const taxoBtnStyle = (val) => {
                        const active = !_taxonomyMode || _taxonomyMode === 'counts';
                        const isActive = val === _taxonomyMode || (val === 'counts' && active);
                        return isActive
                            ? 'background:rgba(99,102,241,0.2); color:#818cf8; cursor:pointer; border:0; border-radius:6px; padding:4px 10px; font-size:0.75rem; font-weight:600; transition:all 0.15s;'
                            : 'background:transparent; color:#9ca3af; cursor:pointer; border:0; border-radius:6px; padding:4px 10px; font-size:0.75rem; font-weight:400; transition:all 0.15s;';
                    };
                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
                                <span>Route Taxonomy by Chain</span>
                                <div class="matrix-filter-group" style="display:inline-flex; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:2px;">
                                    <button onclick="window._taxonomySetMode('counts')" style="${taxoBtnStyle('counts')}">Counts</button>
                                    <button onclick="window._taxonomySetMode('pct')" style="${taxoBtnStyle('pct')}">% of Routes</button>
                                </div>
                            </div>
                            <div style="overflow-x:auto;">
                                <table class="indexer-matrix-table">
                                    <thead>
                                        <tr>
                                            <th style="text-align:left;">Chain</th>
                                            <th style="text-align:right;">Origin/Destination Pairs</th>
                                            <th style="text-align:right;">Routes</th>
                                            <th style="text-align:right;">Daily Stats</th>
                                            <th style="text-align:right;">Distribution Buckets</th>
                                            <th style="text-align:right;">Route Hops</th>
                                        </tr>
                                    </thead>
                                    <tbody id="route-taxonomy-tbody"></tbody>
                                </table>
                            </div>
                        </div>
                    `;
                }

                // Assemble Table Detail Card HTML
                html += `
                    <div class="table-detail-card glass-card" id="table-detail-${tName}">
                        <div class="table-card-top-row">
                            <div class="table-card-title-group">
                                <div class="table-icon-badge">${meta.icon}</div>
                                <div>
                                    <h3 class="table-card-h3">${tName}</h3>
                                    <span class="table-card-category">${meta.title} • ${group.title}</span>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                ${statusPillMarkup}
                                <button class="btn-inspect-sm" onclick="openApiModal('/health/db/table/${tName}')">
                                    Inspect JSON
                                </button>
                            </div>
                        </div>

                        <div class="table-card-meta-bar">
                            <div class="meta-stat-item">
                                <span class="meta-stat-label">Total Record Count</span>
                                <span class="meta-stat-value font-mono">${countDisplay}</span>
                            </div>
                            <div class="meta-stat-item">
                                <span class="meta-stat-label">Freshness SLA Policy</span>
                                <span class="meta-stat-value" style="font-size:0.85rem; color:#d1d5db;">${policyStr}</span>
                            </div>
                            ${rawLatest ? `
                            <div class="meta-stat-item">
                                <span class="meta-stat-label">Latest Record Timestamp</span>
                                <span class="meta-stat-value font-mono" title="${latestFormatted}">${latestAgo || latestFormatted}</span>
                            </div>
                            ` : ''}
                            ${rawEarliest ? `
                            <div class="meta-stat-item">
                                <span class="meta-stat-label">Earliest Historical Record</span>
                                <span class="meta-stat-value font-mono">${earliestFormatted}</span>
                            </div>
                            ` : ''}
                        </div>

                        ${checksHtml ? `
                        <div class="sla-checks-wrapper">
                            <span class="dim-text font-mono" style="font-size:0.75rem;">SLA CHECKS:</span>
                            ${checksHtml}
                        </div>
                        ` : ''}

                        ${breakdownHtml}
                    </div>
                `;
            });
            html += `</div>`;
        });

        tablesDetailContainerEl.innerHTML = html || `<div class="empty-state glass-card">No matching warehouse tables found</div>`;

        // Dynamic taxonomy table rendering (counts / % of Routes)
        if (_taxonomyData) {
            const renderTaxonomyTable = () => {
                const tbody = document.getElementById('route-taxonomy-tbody');
                if (!tbody) return;
                const isPct = _taxonomyMode === 'pct';
                const formatPct = (v, total) => total > 0 ? (v / total * 100).toFixed(1) + '%' : '0.0%';
                const chains = Object.entries(_taxonomyData.chains).sort(([a], [b]) => a.localeCompare(b));
                const renderCell = (value, total, color) => {
                    const text = isPct ? formatPct(value, total) : formatNumber(value);
                    return `<td class="font-mono" style="text-align:right; color:${color}; font-weight:700;">${text}</td>`;
                };
                tbody.innerHTML = chains.map(([chainName, counts]) => {
                    const routes = counts.routes || 1;
                    return `<tr>
                        <td class="font-mono" style="color:#cbd5e1;">${chainName}</td>
                        ${renderCell(counts.pairs, routes, '#67e8f9')}
                        <td class="font-mono" style="text-align:right; color:#a78bfa; font-weight:700;">${formatNumber(counts.routes)}</td>
                        ${renderCell(counts.daily_stats, routes, '#fbbf24')}
                        ${renderCell(counts.route_daily_stats_bucket, routes, '#34d399')}
                        ${renderCell(counts.route_hop, routes, '#f472b6')}
                    </tr>`;
                }).join('');
                const allTotal = _taxonomyData.routes_count || 1;
                tbody.innerHTML += `<tr>
                    <td class="font-mono font-bold" style="border-top:2px solid rgba(99,102,241,0.75); color:#a78bfa;">All Chains</td>
                    ${renderCell(_taxonomyData.pairs_count, allTotal, '#67e8f9')}
                    <td class="font-mono font-bold" style="text-align:right; border-top:2px solid rgba(99,102,241,0.75); color:#a78bfa;">${formatNumber(_taxonomyData.routes_count)}</td>
                    ${renderCell(_taxonomyData.daily_stats_count, allTotal, '#fbbf24')}
                    ${renderCell(_taxonomyData.route_daily_stats_bucket_count, allTotal, '#34d399')}
                    ${renderCell(_taxonomyData.route_hop_count, allTotal, '#f472b6')}
                </tr>`;
            };
            window._taxonomySetMode = (mode) => {
                _taxonomyMode = mode;
                renderTaxonomyTable();
            };
            renderTaxonomyTable();
        }
    };

    // Modal JSON Inspector Handler
    window.openApiModal = async (pathUrl) => {
        if (!apiModal) return;
        const fullUrl = `${window.location.origin}${pathUrl}`;
        modalUrlInput.value = fullUrl;
        modalJsonViewer.innerHTML = `<span class="dim-text">Loading endpoint payload...</span>`;
        apiModal.classList.remove('hidden');

        try {
            const res = await fetch(pathUrl);
            const data = await res.json();
            modalJsonViewer.innerHTML = syntaxHighlightJson(data);
        } catch (err) {
            modalJsonViewer.textContent = `Error fetching payload: ${err.message}`;
        }
    };

    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', () => {
            if (apiModal) apiModal.classList.add('hidden');
        });
    }

    if (apiModal) {
        apiModal.addEventListener('click', (e) => {
            if (e.target === apiModal) apiModal.classList.add('hidden');
        });
    }

    if (copyModalUrlBtn) {
        copyModalUrlBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(modalUrlInput.value).then(() => {
                const origHtml = copyModalUrlBtn.innerHTML;
                copyModalUrlBtn.innerHTML = `<span>Copied!</span>`;
                setTimeout(() => copyModalUrlBtn.innerHTML = origHtml, 1500);
            });
        });
    }

    if (tableSearchInput) {
        tableSearchInput.addEventListener('input', () => {
            if (currentHealthData && currentHealthData.db && currentHealthData.db.table) {
                renderTableDetailCards(currentHealthData.db.table);
            }
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', fetchHealthData);
    }

    function syntaxHighlightJson(json) {
        if (typeof json !== 'string') {
            json = JSON.stringify(json, null, 2);
        }
        json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
            let cls = 'json-number';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) {
                    cls = 'json-key';
                } else {
                    cls = 'json-string';
                }
            } else if (/true|false/.test(match)) {
                cls = 'json-boolean';
            } else if (/null/.test(match)) {
                cls = 'json-null';
            }
            return '<span class="' + cls + '">' + match + '</span>';
        });
    }

    // Initial Fetch & Auto Refresh every 15 minutes (15 * 60 * 1000 = 900000 ms)
    initOdsTokenIcons();
    initOdsTooltips();
    fetchHealthData();
    setInterval(fetchHealthData, 15 * 60 * 1000);
});
