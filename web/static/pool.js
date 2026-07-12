// Pool Explorer Business Logic
(function() {
    let allPools = [];
    const poolListEl = document.getElementById('pool-list');
    const poolDetailEl = document.getElementById('pool-detail');
    const poolSearchEl = document.getElementById('pool-search');

    if (!poolListEl || !poolDetailEl || !poolSearchEl) {
        console.error('Pool Explorer: Required DOM elements not found');
        return;
    }

    const CLUSTER_MAP = {
        '0xe34eb31bfd2afea4320b1ce0d1b8ae943afac425': 'Binance Institutional',
        '0x70ceb22b65490b884d5b81df77fab834764c850c': 'Private HNW',
        '0xfdd37397c8801d9361ad214a169f9d78832a76f2': 'Binance Institutional',
        '0xb0417937d57077e64906f2d93e18a996bd07212b': 'Bybit Professional',
        '0x41f93f35072046ff851214a169f9d78832a76f2': 'Wintermute?',
        '0x6167885a8795a4753093208cc7381f493208cc73': 'Secret Bridge User',
        '0x29141f23351d4289cf30113a34a81b7e42be00523': 'Binance Institutional'
    };

    // Token icon URL helper (uses cryptocurrency-icons CDN with fallback)
    function tokenIconUrl(symbol) {
        const s = symbol.toLowerCase().replace('weth', 'eth').replace('wbtc', 'btc').replace('wbnb', 'bnb');
        return `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${s}.png`;
    }
    function tokenIconHtml(symbol, size = 32) {
        const url = tokenIconUrl(symbol);
        const initial = symbol.charAt(0).toUpperCase();
        return `<img src="${url}" alt="${symbol}" width="${size}" height="${size}"
            style="border-radius:50%;border:2px solid rgba(255,255,255,0.1);vertical-align:middle;background:#1e1e2f;"
            onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
        /><span style="display:none;width:${size}px;height:${size}px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#a855f7);align-items:center;justify-content:center;font-weight:700;font-size:${Math.round(size*0.45)}px;color:#fff;vertical-align:middle;border:2px solid rgba(255,255,255,0.1)">${initial}</span>`;
    }

    const formatUSD = (amount) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 0
        }).format(amount);
    };

    const fetchPools = async () => {
        try {
            console.log('Fetching pools...');
            const response = await fetch('/api/pools');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            allPools = await response.json();
            console.log(`Loaded ${allPools.length} pools`);
            renderPoolList(allPools);
            
            // Check for Auto-selection via URL parameter
            const urlParams = new URLSearchParams(window.location.search);
            const poolId = urlParams.get('id');
            if (poolId) {
                const targetPool = allPools.find(p => p.id == poolId);
                if (targetPool) {
                    selectPool(targetPool);
                    // Also find and highlight in sidebar
                    setTimeout(() => {
                        const items = poolListEl.querySelectorAll('.pool-item');
                        items.forEach((item, idx) => {
                            if (allPools[idx].id == poolId) {
                                item.classList.add('active');
                                item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            }
                        });
                    }, 100);
                }
            }
        } catch (error) {
            console.error('Fetch pools failed:', error);
            poolListEl.innerHTML = `<p style="color:var(--danger)">Error: ${error.message}</p>`;
        }
    };

    const formatFeeTier = (tier) => {
        if (!tier) return 'N/A';
        // If it's already a percentage string from DB, return it
        if (tier.includes('%')) return tier;
        
        const num = parseInt(tier);
        if (isNaN(num)) return tier;
        
        // Uniswap v3/v4 convention: 100 = 0.01%, 500 = 0.05%, 3000 = 0.3%, 10000 = 1%
        return (num / 10000).toFixed(2).replace(/\.00$/, '') + '%';
    };

    const renderPoolList = (pools) => {
        poolListEl.innerHTML = '';
        if (pools.length === 0) {
            poolListEl.innerHTML = '<p style="padding: 1rem; color: var(--text-secondary)">No pools found.</p>';
            return;
        }
        pools.forEach(pool => {
            const div = document.createElement('div');
            div.className = 'pool-item';
            div.innerHTML = `
                <h4>${pool.pool_name}</h4>
                <div class="pool-item-meta">
                    <span class="badge ${pool.network.toLowerCase()}">${pool.network}</span>
                    <span>${formatFeeTier(pool.fee_tier)}</span>
                    <span style="margin-left:auto">${formatUSD(pool.tvl_usd)}</span>
                </div>
            `;
            div.onclick = () => {
                document.querySelectorAll('.pool-item').forEach(i => i.classList.remove('active'));
                div.classList.add('active');
                selectPool(pool);
            };
            poolListEl.appendChild(div);
        });
    };

    const selectPool = async (pool) => {
        poolDetailEl.innerHTML = '<div class="spinner"></div>';
        try {
            const response = await fetch(`/api/pools/${pool.id}/leaderboard`);
            const leaderboard = await response.json();
            renderLeaderboard(pool, leaderboard);
        } catch (error) {
            poolDetailEl.innerHTML = `<p style="color:var(--danger)">Error: ${error.message}</p>`;
        }
    };

    const renderLeaderboard = (pool, leaderboard) => {
        let html = `
            <div class="leaderboard-header">
                <div>
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px">
                        <div style="display:flex;align-items:center">
                            ${pool.tokens && pool.tokens[0] ? tokenIconHtml(pool.tokens[0], 44) : ''}
                            <span style="margin-left:-12px">${pool.tokens && pool.tokens[1] ? tokenIconHtml(pool.tokens[1], 44) : ''}</span>
                        </div>
                        <h2 class="pool-title-large" style="margin:0">${pool.pool_name}</h2>
                    </div>
                    <div class="pool-stats-row">
                        <div class="pool-stat-box">
                            <span class="pool-stat-label">Total Pool TVL</span>
                            <span class="pool-stat-value">${formatUSD(pool.tvl_usd)}</span>
                        </div>
                        <div class="pool-stat-box">
                            <span class="pool-stat-label">24h Volume</span>
                            <span class="pool-stat-value">${formatUSD(pool.volume_24h)}</span>
                        </div>
                        <div class="pool-stat-box">
                            <span class="pool-stat-label">Fee Tier</span>
                            <span class="pool-stat-value">${formatFeeTier(pool.fee_tier)}</span>
                        </div>
                    </div>
                </div>
                <div style="text-align: right">
                    <span class="pool-stat-label">Protocol</span>
                    <div class="pool-stat-value" style="color: var(--uniswap-pink)">${pool.protocol}</div>
                </div>
            </div>

            <h3 style="margin-bottom: 1.5rem;">LP Provider Leaderboard</h3>
            <table class="leaderboard-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Provider Wallet</th>
                        <th>Holding Value</th>
                        <th>Pool Share</th>
                        <th>Positions</th>
                        <th>Last Action</th>
                    </tr>
                </thead>
                <tbody>
        `;

        if (leaderboard.length === 0) {
            html += `
                <tr>
                    <td colspan="6" style="text-align: center; padding: 4rem; color: var(--text-secondary);">
                        <p style="margin-bottom: 1.5rem;">No LP positions currently indexed for this pool.</p>
                        <button onclick="window.triggerSync(${pool.id})" class="btn-primary" id="sync-btn-empty">
                            <i class="fas fa-sync"></i> Trigger Discovery Sync
                        </button>
                    </td>
                </tr>
            `;
        }

        leaderboard.forEach((lp, index) => {
            const walletLower = lp.wallet_address.toLowerCase();
            const cluster = CLUSTER_MAP[walletLower];
            const clusterTag = cluster ? `<span class="cluster-tag">${cluster}</span>` : '';
            
            const lastActive = lp.last_activity ? new Date(lp.last_activity).toLocaleDateString() : 'N/A';
            const share = lp.share_percent.toFixed(2);

            html += `
                <tr class="leaderboard-row">
                    <td class="rank-cell">#${index + 1}</td>
                    <td class="wallet-cell">
                        <div class="wallet-container">
                            <code class="wallet-address" title="${lp.wallet_address}">${lp.wallet_address.slice(0, 6)}...${lp.wallet_address.slice(-4)}</code>
                            <button class="copy-addr-btn" onclick="copyToClipboard('${lp.wallet_address}', this)" title="Copy full address">
                                <i class="fas fa-copy"></i>
                            </button>
                        </div>
                        ${clusterTag}
                    </td>
                    <td><span style="font-weight:600">${formatUSD(lp.balance_usd)}</span></td>
                    <td>
                        <div style="display:flex; align-items:center; gap: 10px;">
                            <span style="width: 45px">${share}%</span>
                            <div class="share-bar-container">
                                <div class="share-bar" style="width: ${share}%"></div>
                            </div>
                        </div>
                    </td>
                    <td>${lp.position_count} NFTs</td>
                    <td style="color: var(--text-secondary); font-size: 0.9rem;">${lastActive}</td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;

        poolDetailEl.innerHTML = html;
    };

    poolSearchEl.addEventListener('input', (e) => {
        const val = e.target.value.toLowerCase();
        const filtered = allPools.filter(p => p.pool_name.toLowerCase().includes(val));
        renderPoolList(filtered);
    });

    // --- Toast notification helper ---
    window.showToast = function(message, type = 'success') {
        // Ensure container exists
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:10px;pointer-events:none;';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        const colors = {
            success: { bg: 'rgba(16, 185, 129, 0.15)', border: '#10b981', icon: '✓' },
            error:   { bg: 'rgba(239, 68, 68, 0.15)',  border: '#ef4444', icon: '✗' },
            info:    { bg: 'rgba(99, 102, 241, 0.15)',  border: '#6366f1', icon: 'ℹ' }
        };
        const c = colors[type] || colors.info;
        toast.style.cssText = `
            pointer-events:auto; padding:14px 20px; border-radius:10px;
            background:${c.bg}; border:1px solid ${c.border};
            backdrop-filter:blur(12px); color:#e2e8f0; font-size:0.9rem;
            display:flex; align-items:center; gap:10px; min-width:280px; max-width:420px;
            box-shadow:0 8px 32px rgba(0,0,0,0.3);
            transform:translateX(120%); transition:transform 0.35s cubic-bezier(.22,1,.36,1), opacity 0.3s;
            opacity:0;
        `;
        toast.innerHTML = `<span style="font-size:1.1rem">${c.icon}</span><span>${message}</span>`;
        container.appendChild(toast);
        // Slide in
        requestAnimationFrame(() => { toast.style.transform = 'translateX(0)'; toast.style.opacity = '1'; });
        // Auto-dismiss
        setTimeout(() => {
            toast.style.transform = 'translateX(120%)';
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 400);
        }, 4000);
    }

    window.copyToClipboard = (text, btn) => {
        navigator.clipboard.writeText(text).then(() => {
            const icon = btn.querySelector('i');
            const originalClass = icon.className;
            icon.className = 'fas fa-check';
            icon.style.color = 'var(--success)';
            
            showToast('Address copied to clipboard', 'success');
            
            setTimeout(() => {
                icon.className = originalClass;
                icon.style.color = '';
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy: ', err);
            showToast('Failed to copy address', 'error');
        });
    };

    window.triggerSync = async (poolId) => {
        const btn = document.getElementById('sync-btn-empty');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing from Graph...';
        }
        showToast('Syncing positions from The Graph...', 'info');

        try {
            const response = await fetch(`/api/pools/${poolId}/sync`, { 
                method: 'POST',
                headers: {
                    'Authorization': 'Basic ' + btoa('admin:chaintelligence77')
                }
            });
            const result = await response.json();
            
            if (result.status === 'success') {
                const pool = allPools.find(p => p.id == poolId);
                if (pool) selectPool(pool);
                showToast(result.message, 'success');
            } else {
                showToast(`Sync failed: ${result.detail || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            showToast(`Error: ${error.message}`, 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-sync"></i> Trigger Discovery Sync';
            }
        }
    };

    // Initial fetch
    fetchPools();
})();
