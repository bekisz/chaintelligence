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
        'liquidity_pool',
        'liquidity_pool_history',
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
            id: 'pools-group',
            title: 'Liquidity Pool',
            icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>',
            tables: ['liquidity_pool', 'liquidity_pool_history']
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
        'liquidity_pool': { title: 'Liquidity Pools', category: 'Liquidity Pool', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>', defaultPolicy: 'Active DEX pool registry' },
        'liquidity_pool_history': { title: 'Pool History Metrics', category: 'Liquidity Pool', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>', defaultPolicy: 'Daily TVL & Volume metrics within 2 days' },
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

    // TOP Summary Matrix rendering (Amber / Green code for ordered active tables)
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
                if (tName === 'swaps' && tData.chains) {
                    const chainNames = Object.keys(tData.chains).sort((a, b) => {
                        if (a.toLowerCase() === 'ethereum') return -1;
                        if (b.toLowerCase() === 'ethereum') return 1;
                        return a.localeCompare(b);
                    });

                    // Gather distinct protocol names across all chains
                    const protoSet = new Set();
                    chainNames.forEach(cName => {
                        const protos = tData.chains[cName].protocols || {};
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

                    breakdownHtml += `
                        <div class="breakdown-subpanel">
                            <div class="subpanel-title">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
                                Staleness Matrix
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
                            const pData = (tData.chains[cName].protocols || {})[protoName];
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
                        </div>
                    `;
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
                }

                // 5. Coin price history coverage breakdown
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
                if (tName === 'liquidity_pool_history' && tables.liquidity_pool) {
                    const activeVolFilter = window.currentMatrixVolFilter || '0';
                    const matrixData = (tables.liquidity_pool.volume_filters && tables.liquidity_pool.volume_filters[activeVolFilter]) ? tables.liquidity_pool.volume_filters[activeVolFilter] : (tData.covered_pools ? tData : tables.liquidity_pool);
                    const cp = matrixData.covered_pools || tData.covered_pools;
                    if (cp) {
                        const formatVolLabel = (v) => {
                            if (v === '0') return 'All Pools';
                            if (v === '1000') return '> $1k 7d Vol';
                            if (v === '100000') return '> $100k 7d Vol';
                            if (v === '10000000') return '> $10M 7d Vol';
                            return `> $${v} 7d Vol`;
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
    fetchHealthData();
    setInterval(fetchHealthData, 15 * 60 * 1000);
});
