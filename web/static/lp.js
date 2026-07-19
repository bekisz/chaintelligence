// Global state for filtering and sorting
let allPositions = [];
let lpTokenImageMap = {};
let lpTokenSlugMap = {};

const getCmcUrl = (symbol) => {
    const s = (symbol || '').toUpperCase().trim();
    const slug = lpTokenSlugMap[s] || s.toLowerCase();
    return `https://coinmarketcap.com/currencies/${slug}/`;
};

// Brand mark SVGs (parity with Pool Explorer's pool-label-link icons).
const LP_UNI_SVG = `<svg class="proto-brand-icon" viewBox="0 0 438 504" fill="#FF007A" xmlns="http://www.w3.org/2000/svg"><path d="M171.43,114.54c-5.45-.78-5.71-1-3.12-1.3,4.94-.78,16.37.26,24.42,2.08,18.7,4.41,35.58,15.84,53.5,35.84l4.68,5.46,6.75-1c28.83-4.68,58.44-1,83.11,10.39,6.76,3.11,17.41,9.35,18.7,10.9.52.52,1.3,3.9,1.82,7.28,1.82,12.2,1,21.29-2.85,28.31-2.08,3.89-2.08,4.93-.78,8.31a7.79,7.79,0,0,0,7,4.41c6.23,0,12.73-9.87,15.84-23.63l1.3-5.46,2.34,2.6c13.24,14.81,23.63,35.32,25.19,49.87l.52,3.89-2.34-3.37c-3.89-6-7.53-9.87-12.46-13.25-8.83-6-18.18-7.79-42.86-9.09-22.33-1.3-35.06-3.11-47.53-7.27-21.3-7-32.2-16.1-57.4-49.61-11.17-14.8-18.18-22.85-25.19-29.61C206.75,125.45,191.43,117.66,171.43,114.54Z"/><path d="M364.93,147.53c.52-9.87,1.82-16.37,4.67-22.34,1-2.34,2.08-4.42,2.34-4.42s-.26,1.82-1,3.9c-2.08,5.71-2.34,13.76-1,22.86,1.82,11.68,2.6,13.24,15.07,26,5.71,6,12.46,13.5,15.06,16.62l4.42,5.71L400,191.68c-5.45-5.2-17.92-15.07-20.78-16.36-1.81-1-2.07-1-3.37.26-1,1-1.3,2.59-1.3,10.12-.26,11.69-1.82,19-5.72,26.5-2.07,3.89-2.33,3.11-.52-1.3,1.3-3.38,1.56-4.94,1.56-16.1,0-22.6-2.59-28.06-18.44-37.15-3.89-2.33-10.65-5.71-14.54-7.53a57.93,57.93,0,0,1-7-3.37c.51-.52,15.84,3.89,21.81,6.49,9.09,3.64,10.65,3.89,11.69,3.64C364.15,156.1,364.67,154,364.93,147.53Z"/><path d="M182.08,186.22c-10.91-15.06-17.92-38.44-16.36-55.84l.52-5.45,2.59.52a60.93,60.93,0,0,1,16.63,6.23c10.39,6.24,15.06,14.81,19.48,36.1,1.29,6.24,3.11,13.51,3.89,15.85,1.3,3.89,6.24,13,10.39,18.7,2.86,4.15,1,6.23-5.45,5.71C203.9,207,190.65,197.91,182.08,186.22Z"/><path d="M351.68,299.21c-51.42-20.78-69.6-38.7-69.6-69.09,0-4.42.25-8.05.25-8.05a49.86,49.86,0,0,1,4.42,3.37c10.39,8.31,22.08,11.95,54.54,16.63,19,2.85,29.87,4.93,39.74,8.31,31.43,10.39,50.91,31.68,55.58,60.51,1.3,8.31.52,24.16-1.56,32.47-1.81,6.49-7,18.44-8.31,18.7-.26,0-.78-1.3-.78-3.38-.52-10.91-6-21.29-15.06-29.35C400,320,386,313,351.68,299.21Z"/><path d="M315.32,307.78a61.45,61.45,0,0,0-2.6-10.91l-1.3-3.9,2.34,2.86c3.38,3.9,6,8.57,8.31,15.06,1.82,4.94,1.82,6.5,1.82,14.55,0,7.79-.26,9.61-1.82,14a46.86,46.86,0,0,1-10.91,17.41c-9.35,9.61-21.55,14.8-39,17.14-3.12.26-11.95,1-19.74,1.56-19.48,1-32.47,3.11-44.16,7.27-1.56.52-3.11,1-3.37.78-.52-.52,7.53-5.2,14-8.31,9.09-4.42,18.44-6.76,39-10.39,10.13-1.56,20.52-3.64,23.12-4.68C306.75,352.19,319.48,332.19,315.32,307.78Z"/><path d="M339,349.59q-10.14-22.2-4.68-42.07c.52-1.3,1-2.6,1.56-2.6a11.07,11.07,0,0,1,3.63,1.82c3.12,2.08,9.61,5.71,26.24,14.8,21,11.43,33,20.26,41.29,30.39,7.28,8.83,11.69,19,13.77,31.43,1.3,7,.52,23.89-1.3,30.9-5.71,22.08-18.7,39.74-37.66,49.87a36.28,36.28,0,0,1-5.45,2.6c-.26,0,.78-2.6,2.33-5.71,6.24-13.25,7-26,2.34-40.26-2.86-8.83-8.83-19.48-20.78-37.4C346,362.58,342.59,357.12,339,349.59Z"/><path d="M145.46,429.07c19.22-16.1,42.85-27.53,64.67-31.17,9.35-1.56,24.93-1,33.51,1.3,13.76,3.64,26.23,11.43,32.72,21,6.23,9.35,9.09,17.4,11.95,35.32,1,7,2.34,14.29,2.6,15.84,2.07,9.36,6.23,16.63,11.42,20.52,8.06,6,22.08,6.24,35.85,1,2.33-.78,4.41-1.56,4.41-1.3.52.52-6.49,5.2-11.17,7.54a36.81,36.81,0,0,1-18.7,4.41c-12.46,0-23.11-6.49-31.68-19.48-1.82-2.6-5.46-10.13-8.57-17.14-9.1-21-13.77-27.27-24.42-34.28-9.35-6-21.3-7.28-30.39-2.86-11.94,5.71-15.06,21-6.75,30.39,3.38,3.89,9.61,7,14.8,7.79a15.86,15.86,0,0,0,17.93-15.85c0-6.23-2.34-9.86-8.58-12.72-8.31-3.64-17.4.52-17.14,8.57,0,3.38,1.56,5.45,4.94,7,2.08,1,2.08,1,.52.78-7.54-1.56-9.35-10.91-3.38-16.88,7.27-7.27,22.6-4.16,27.79,6,2.08,4.16,2.34,12.47.52,17.66C243.9,474,231.43,480,218.7,476.6c-8.57-2.34-12.21-4.68-22.59-15.32-18.19-18.7-25.2-22.34-51.17-26.24l-4.94-.78Z"/><path fill-rule="evenodd" d="M8.84,11.17C69.36,84.67,162.6,199,167.28,205.18c3.89,5.2,2.33,10.13-4.16,13.77-3.64,2.08-11.17,4.16-14.8,4.16a18.74,18.74,0,0,1-12.47-5.46c-2.34-2.34-12.47-17.14-35.32-52.72-17.41-27.27-32.21-49.87-32.47-50.13-1-.52-1-.52,30.65,56.1,20,35.58,26.49,48.31,26.49,49.87,0,3.37-1,5.19-5.19,9.87-7,7.79-10.13,16.62-12.47,35.06-2.6,20.52-9.61,35.06-29.61,59.74C66.24,340,64.42,342.58,61.57,348.55c-3.64,7.28-4.68,11.43-5.2,20.78-.52,9.87.52,16.11,3.38,25.46,2.6,8.31,5.45,13.76,12.47,24.41,6,9.35,9.61,16.36,9.61,19,0,2.08.52,2.08,9.87,0,22.33-5.19,40.77-14,50.9-24.93,6.24-6.76,7.79-10.39,7.79-19.74,0-6-.26-7.28-1.81-10.91-2.6-5.72-7.54-10.39-18.19-17.66-14-9.61-20-17.41-21.55-27.79-1.3-8.83.26-14.81,8-31.17,8-16.88,10.13-23.9,11.43-41,.78-10.91,2.07-15.32,5.19-18.7,3.38-3.63,6.23-4.93,14.29-6,13.24-1.82,21.81-5.2,28.57-11.69,6-5.45,8.57-10.91,8.83-19l.26-6L182.08,200C169.87,186,.79,0,0,0-.25,0,3.91,4.93,8.84,11.17Z"/><path fill-rule="evenodd" d="M193.77,243.88c-6.24,1.82-12.21,8.57-14,15.33-1,4.15-.52,11.69,1.3,14,2.86,3.64,5.46,4.68,12.73,4.68,14.28,0,26.49-6.24,27.79-13.77,1.3-6.23-4.16-14.8-11.69-18.7C206,243.36,197.92,242.59,193.77,243.88Z"/></svg>`;

const LP_PANCAKE_SVG = `<svg class="proto-brand-icon" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><ellipse cx="50" cy="72" rx="26" ry="18" fill="#d1884f" opacity="0.9"/><ellipse cx="50" cy="68" rx="22" ry="14" fill="#d1884f"/><ellipse cx="36" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(-15 36 32)"/><ellipse cx="64" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(15 64 32)"/><ellipse cx="50" cy="58" rx="22" ry="20" fill="#d1884f"/><ellipse cx="50" cy="60" rx="18" ry="16" fill="#d1884f"/><circle cx="42" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/><circle cx="58" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/></svg>`;

// Revert Finance link. For Uniswap V3/V4 we link to the specific LP POSITION
// (NFT token id): revert.finance/#/<uniswapv4-position|uniswap-position>/<net>/<tokenId>.
// Other protocols fall back to the pool-based URL where Revert supports it.
const revertLink = (poolAddress, protocol, network, tokenId) => {
    const proto = (protocol || '').toLowerCase();
    const net = (network || 'Ethereum').toLowerCase();
    let revertNet = 'mainnet';
    if (net.includes('base')) revertNet = 'base';
    else if (net.includes('arbitrum')) revertNet = 'arbitrum';
    else if (net.includes('optimism')) revertNet = 'optimism';
    else if (net.includes('polygon')) revertNet = 'polygon';
    else if (net.includes('bnb') || net.includes('bsc')) revertNet = 'bnb';

    if (tokenId && proto.includes('uniswap')) {
        const sub = proto.includes('v4') ? 'uniswapv4-position' : 'uniswap-position';
        return `<a href="https://revert.finance/#/${sub}/${revertNet}/${tokenId}" target="_blank" class="revert-link" data-tooltip="Analyze position on Revert Finance" onclick="event.stopPropagation();"><img src="/static/assets/revert.svg" alt="Revert Finance" class="revert-icon" /></a>`;
    }

    if (poolAddress && /^0x[a-f0-9]{40}$/i.test(poolAddress)) {
        let revertProto = '';
        if (proto.includes('pancakeswap') && proto.includes('v3') && (revertNet === 'bnb' || revertNet === 'arbitrum')) revertProto = 'pancakeswapv3';
        else if (proto.includes('aerodrome') && revertNet === 'base') revertProto = 'aerodrome';
        if (revertProto) {
            return `<a href="https://revert.finance/#/pool/${revertNet}/${revertProto}/${poolAddress.toLowerCase()}" target="_blank" class="revert-link" data-tooltip="Analyze on Revert Finance" onclick="event.stopPropagation();"><img src="/static/assets/revert.svg" alt="Revert Finance" class="revert-icon" /></a>`;
        }
    }
    return '';
};

// Build Uniswap/PancakeSwap + DexScreener + Revert + DeFi Llama link panes for the arrow.
const renderPoolLinks = (poolAddress, protocol, network, defillamaUuid, tokenId) => {
    const proto = (protocol || '').toLowerCase();
    const net = (network || 'Ethereum').toLowerCase();
    const isRealAddr = typeof poolAddress === 'string' && /^0x[a-f0-9]{40}$/i.test(poolAddress);
    const isV4Id = typeof poolAddress === 'string' && /^0x[a-f0-9]{64}$/i.test(poolAddress);
    let links = '';
    const defillamaHtml = defillamaUuid
        ? `<a href="https://defillama.com/yields/pool/${defillamaUuid}" target="_blank" class="lp-link defillama-link" data-tooltip="View on DeFi Llama" onclick="event.stopPropagation();"><img src="/static/assets/defillama.ico" alt="DeFi Llama" class="lp-link-icon defillama-icon" style="border-radius: 50%;" /></a>`
        : '';

    // V4: bytes32 poolId -> explorer + DexScreener + Revert links.
    // Verified: DexScreener and Revert both index V4 pools by the bytes32
    // poolId (dexscreener.com/<chain>/<poolId>,
    // revert.finance/#/pool/<net>/uniswapv4/<poolId>). Revert V4 is
    // Ethereum/Unichain only.
    if (isV4Id) {
        let dsNet = 'ethereum';
        if (net.includes('base')) dsNet = 'base';
        else if (net.includes('arbitrum')) dsNet = 'arbitrum';
        else if (net.includes('optimism')) dsNet = 'optimism';
        else if (net.includes('polygon')) dsNet = 'polygon';
        else if (net.includes('bnb') || net.includes('bsc')) dsNet = 'bsc';

        if (proto.includes('pancake')) {
            let pChain = 'bsc';
            if (net.includes('base')) pChain = 'base';
            else if (net.includes('eth')) pChain = 'eth';
            else if (net.includes('arbitrum')) pChain = 'arb';
            links += `<a href="https://pancakeswap.finance/liquidity/pool/${pChain}/${poolAddress}" target="_blank" class="pool-label-link pool-label-link--pancakeswap" data-tooltip="View on PancakeSwap" onclick="event.stopPropagation();">${LP_PANCAKE_SVG}</a>`;
        } else if (proto.includes('uniswap') || proto.includes('v4')) {
            let uniNetwork = 'ethereum';
            if (net.includes('base')) uniNetwork = 'base';
            else if (net.includes('bnb') || net.includes('bsc')) uniNetwork = 'bnb';
            else if (net.includes('arbitrum')) uniNetwork = 'arbitrum';
            links += `<a href="https://app.uniswap.org/explore/pools/${uniNetwork}/${poolAddress}" target="_blank" class="pool-label-link pool-label-link--uniswap" data-tooltip="View on Uniswap" onclick="event.stopPropagation();">${LP_UNI_SVG}</a>`;
            links += revertLink(poolAddress, protocol, network, tokenId);
        }

        links += `<a href="https://dexscreener.com/${dsNet}/${poolAddress}" target="_blank" class="lp-link dexscreener-link" data-tooltip="View on DexScreener" onclick="event.stopPropagation();"><img src="/static/assets/dexscreener.ico" alt="DexScreener" class="lp-link-icon dexscreener-icon" style="border-radius: 50%;" /></a>`;
        links += defillamaHtml;

        return links ? `<div class="label-pane links-pane">${links}</div>` : '';
    }

    if (isRealAddr) {
        if (proto.includes('pancake')) {
            let pChain = 'bsc';
            if (net.includes('base')) pChain = 'base';
            else if (net.includes('eth')) pChain = 'eth';
            else if (net.includes('arbitrum')) pChain = 'arb';
            const href = proto.includes('v4')
                ? `https://pancakeswap.finance/liquidity/pool/${pChain}/${poolAddress}`
                : `https://pancakeswap.finance/info/v3/pairs/${poolAddress}?chain=${pChain}`;
            links += `<a href="${href}" target="_blank" class="pool-label-link pool-label-link--pancakeswap" data-tooltip="View on PancakeSwap" onclick="event.stopPropagation();">${LP_PANCAKE_SVG}</a>`;
        } else if (proto.includes('uniswap') || proto.includes('v3') || proto.includes('v2')) {
            let uniNetwork = 'ethereum';
            if (net.includes('base')) uniNetwork = 'base';
            else if (net.includes('bnb') || net.includes('bsc')) uniNetwork = 'bnb';
            else if (net.includes('arbitrum')) uniNetwork = 'arbitrum';
            links += `<a href="https://app.uniswap.org/explore/pools/${uniNetwork}/${poolAddress}" target="_blank" class="pool-label-link pool-label-link--uniswap" data-tooltip="View on Uniswap" onclick="event.stopPropagation();">${LP_UNI_SVG}</a>`;
        }
    }

    if (isRealAddr) {
        links += revertLink(poolAddress, protocol, network, tokenId);
    }

    if (isRealAddr) {
        let dsNet = 'ethereum';
        if (net.includes('base')) dsNet = 'base';
        else if (net.includes('arbitrum')) dsNet = 'arbitrum';
        else if (net.includes('optimism')) dsNet = 'optimism';
        else if (net.includes('polygon')) dsNet = 'polygon';
        else if (net.includes('bnb') || net.includes('bsc')) dsNet = 'bsc';
        links += `<a href="https://dexscreener.com/${dsNet}/${poolAddress}" target="_blank" class="lp-link dexscreener-link" data-tooltip="View on DexScreener" onclick="event.stopPropagation();"><img src="/static/assets/dexscreener.ico" alt="DexScreener" class="lp-link-icon dexscreener-icon" style="border-radius: 50%;" /></a>`;
    }

    links += defillamaHtml;
    return links ? `<div class="label-pane links-pane">${links}</div>` : '';
};

let currentFilters = {
    network: 'all',
    protocol: 'all',
    wallet: 'all',
    search: ''
};
let currentSort = 'value-desc';

document.addEventListener('DOMContentLoaded', () => {
    const totalPortfolioValueEl = document.getElementById('total-portfolio-value');
    const totalRewardsEl = document.getElementById('total-rewards');
    const positionsGrid = document.getElementById('positions-grid');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');

    // Filter and sort controls
    const networkFilter = document.getElementById('network-filter');
    const protocolFilter = document.getElementById('protocol-filter');
    const walletFilter = document.getElementById('wallet-filter');
    const searchFilter = document.getElementById('search-filter');
    const sortBy = document.getElementById('sort-by');

    const formatUSD = (amount) => {
        const n = Number(amount);
        if (isNaN(n)) return '$0.00';

        let digits = 2;
        if (n > 100) digits = 0; // Round to integer if > 100

        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        }).format(n);
    };

    const formatTokenAmount = (num) => {
        if (!num) return '0.00';
        const n = Number(num);
        if (n === 0) return '0.00';

        if (n > 100) {
            // Round to integer if > 100
            return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
        }

        if (n >= 1) {
            return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        // Small numbers: 2 significant digits
        return n.toLocaleString('en-US', { maximumSignificantDigits: 2 });
    };

    const cleanLabel = (label) => {
        if (!label) return 'Position';
        // Remove (Token ID: ...) completely
        return label.replace(/\(Token ID:\s*([^\)]+)\)/i, '').replace(/\s*\/\s*/g, ' - ').trim();
    };

    const applyFiltersAndSort = () => {
        let filtered = [...allPositions];

        // Apply network filter
        if (currentFilters.network !== 'all') {
            filtered = filtered.filter(p => p.network === currentFilters.network);
        }

        // Apply protocol filter
        if (currentFilters.protocol !== 'all') {
            filtered = filtered.filter(p => p.protocol === currentFilters.protocol);
        }

        // Apply wallet filter
        if (currentFilters.wallet !== 'all') {
            filtered = filtered.filter(p => p.address === currentFilters.wallet);
        }

        // Apply search filter
        if (currentFilters.search) {
            const search = currentFilters.search.toLowerCase();
            filtered = filtered.filter(p => {
                const label = cleanLabel(p.position_label).toLowerCase();
                return label.includes(search);
            });
        }

        // Apply "Hide Closed" filter
        const hideClosed = document.getElementById('hide-closed-filter')?.checked;
        if (hideClosed) {
            filtered = filtered.filter(p => !p.isClosed);
        }

        // Apply sorting
        switch (currentSort) {
            case 'value-desc':
                filtered.sort((a, b) => b.balance_usd - a.balance_usd);
                break;
            case 'value-asc':
                filtered.sort((a, b) => a.balance_usd - b.balance_usd);
                break;
            case 'rewards-desc':
                filtered.sort((a, b) => b.total_unclaimed_usd - a.total_unclaimed_usd);
                break;
            case 'rewards-asc':
                filtered.sort((a, b) => a.total_unclaimed_usd - b.total_unclaimed_usd);
                break;
            case 'pair':
                filtered.sort((a, b) => {
                    const labelA = cleanLabel(a.position_label);
                    const labelB = cleanLabel(b.position_label);
                    return labelA.localeCompare(labelB);
                });
                break;
        }

        renderPositions(filtered);
    };

    // Modal Elements
    const modal = document.getElementById('history-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalLoader = document.getElementById('modal-loader');
    const closeModal = document.querySelector('.close-modal');

    // Close Modal Logic
    if (closeModal) {
        closeModal.onclick = () => {
            modal.style.display = "none";
        };
    }

    window.onclick = (event) => {
        if (event.target == modal) {
            modal.style.display = "none";
        }
    };

    // Global function to open history (attached to window to be accessible from inline onclick)
    window.openHistoryModal = async (positionKey, positionLabel) => {
        if (!modal) return;

        modal.style.display = "block";
        modalTitle.textContent = `History: ${positionLabel}`;
        modalBody.innerHTML = '';
        modalLoader.classList.remove('hidden');

        try {
            const response = await fetch(`/api/lp/history?position_key=${encodeURIComponent(positionKey)}`);
            if (!response.ok) throw new Error('Failed to fetch history');

            const historyData = await response.json();

            if (!historyData || historyData.length === 0) {
                modalBody.innerHTML = '<p style="text-align:center; color:var(--text-secondary);">No history data available.</p>';
                return;
            }

            // Helper for Explorer URLs
            const getExplorerUrl = (network, txHash) => {
                if (!network || !txHash) return '#';
                switch (network) {
                    case 'Ethereum': return `https://etherscan.io/tx/${txHash}`;
                    case 'Arbitrum': return `https://arbiscan.io/tx/${txHash}`;
                    case 'Base': return `https://basescan.org/tx/${txHash}`;
                    case 'Polygon': return `https://polygonscan.com/tx/${txHash}`;
                    case 'Optimism': return `https://optimistic.etherscan.io/tx/${txHash}`;
                    default: return '#';
                }
            };

            // Helper for Event Type Styling
            const getEventStyle = (type) => {
                if (!type) return {};
                switch (type) {
                    case 'create':
                        return { label: 'Position Created', color: 'var(--success)' };
                    case 'add_liquidity':
                        return { label: 'Add Liquidity', color: 'var(--success)' };
                    case 'withdraw':
                    case 'delete':
                        return { label: 'Withdraw', color: 'var(--danger)' };
                    case 'collect_claim':
                        return { label: 'Claim', color: '#3b82f6' }; // Blue
                    default:
                        return { label: type, color: 'var(--text-primary)' };
                }
            };

            // Identify tokens from first row (assuming consistent pool)
            const coin0 = historyData[0].coin0 || 'Asset 0';
            const coin1 = historyData[0].coin1 || 'Asset 1';

            // Build Table
            let tableHtml = `
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Type</th>
                            <th>${coin0} Amount</th>
                            <th>${coin1} Amount</th>
                            <th>Tx</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            historyData.forEach(row => {
                const date = new Date(row.timestamp).toLocaleString();
                const style = getEventStyle(row.event_type);
                const explorerUrl = getExplorerUrl(row.network, row.tx_hash);

                tableHtml += `
                    <tr>
                        <td style="font-size:0.8rem; color:var(--text-secondary);">${date}</td>
                        <td style="color:${style.color}; font-weight:600;">${style.label}</td>
                        <td>${formatTokenAmount(row.amount0)}</td>
                        <td>${formatTokenAmount(row.amount1)}</td>
                        <td>
                            <a href="${explorerUrl}" target="_blank" style="color:var(--accent); text-decoration:none;">
                                <i class="fas fa-external-link-alt"></i> View
                            </a>
                        </td>
                    </tr>
                `;
            });

            tableHtml += `
                    </tbody>
                </table>
            `;

            modalBody.innerHTML = tableHtml;

        } catch (error) {
            console.error(error);
            modalBody.innerHTML = `<p style="color:var(--danger);">Error loading history: ${error.message}</p>`;
        } finally {
            modalLoader.classList.add('hidden');
        }
    };

    fetch('/api/coin/list')
        .then(response => response.json())
        .then(coins => coins.forEach(coin => {
            if (!coin.symbol) return;
            const up = coin.symbol.toUpperCase();
            if (coin.image) lpTokenImageMap[up] = coin.image;
            if (coin.slug) lpTokenSlugMap[up] = coin.slug;
        }))
        .catch(error => console.warn('Unable to load LP token icons:', error));

    const getProtocolClass = (protocol) => {
        const value = (protocol || '').toLowerCase();
        if (value.includes('pancake') && value.includes('v4')) return 'pancakeswap-v4';
        if (value.includes('pancake')) return 'pancakeswap-v3';
        if (value.includes('v4')) return 'v4';
        if (value.includes('v2')) return 'uniswap-v2';
        if (value.includes('aerodrome')) return 'aerodrome';
        return 'v3';
    };

    const renderPairPath = (asset0, asset1, images, protocol, feeTier, apr7d, poolAddress, network, defillamaUuid, tokenId) => {
        const aliases = { WETH: 'ETH', WBTC: 'BTC', CBBTC: 'BTC', TBTC: 'BTC', KBTC: 'BTC', LBTC: 'BTC', FBTC: 'BTC', WBNB: 'BNB' };
        if (!Array.isArray(images)) {
            try { images = JSON.parse(images || '[]'); } catch { images = []; }
        }
        const token = (asset, image) => {
            const symbol = (asset.symbol || '---').toUpperCase();
            const mapped = aliases[symbol] || symbol;
            const source = image || lpTokenImageMap[symbol] || lpTokenImageMap[mapped] || `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${mapped.toLowerCase()}.png`;
            return `<a href="${getCmcUrl(symbol)}" target="_blank" class="token-badge-link" data-tooltip="${symbol} on CoinMarketCap" onclick="event.stopPropagation();"><span class="token-badge"><img src="${source}" width="20" height="20" alt="${symbol}" style="border-radius: 50%; vertical-align: middle; flex-shrink: 0;" onerror="this.onerror=null;this.src='/static/favicon.png'"> ${symbol}</span></a>`;
        };
        const fee = feeTier || 'LP';
        const apr = apr7d ? `${(apr7d * 100).toFixed(2)}% 7d` : 'APR pending';
        const linksPane = renderPoolLinks(poolAddress, protocol, network, defillamaUuid, tokenId);
        return `<div class="lp-pair-path ${getProtocolClass(protocol)}" aria-label="${asset0.symbol} to ${asset1.symbol} liquidity pair">
            ${token(asset0, images[0])}
            <div class="lp-pair-arrow" aria-hidden="true"><svg viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="1.8" style="transform: scaleX(-1);"><polyline points="1,1 7,7 1,13"/></svg><div class="lp-pair-line"><div class="lp-pair-label"><span>${fee}</span><span>${apr}</span>${linksPane}</div></div><svg viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="1,1 7,7 1,13"/></svg></div>
            ${token(asset1, images[1])}
        </div>`;
    };

    const renderPositions = (positions) => {
        if (!positions || positions.length === 0) {
            positionsGrid.innerHTML = '<div class="no-data-msg">No positions match your filters.</div>';
            return;
        }

        positionsGrid.innerHTML = '';

        positions.forEach(pos => {
            const row = document.createElement('div');
            // Remove 'glass' to avoid conflict with new solid theme
            row.className = `position-row ${pos.isClosed ? 'closed-pos' : ''}`;
            if (pos.isClosed) row.style.opacity = '0.6';

            const timeStr = new Date(pos.timestamp).toLocaleString();
            let cleanedLabel = cleanLabel(pos.position_label);

            const walletAddr = pos.address || '';
            const walletDisplay = walletAddr.length > 4 ? `...${walletAddr.slice(-4)}` : walletAddr;

            let displayLabel = cleanedLabel;
            if (pos.isClosed) {
                displayLabel += ' <span style="color:#f87171; font-size:0.8em;">(Closed)</span>';
            }

            const delta = pos.reward_delta_usd || 0;
            const accrualHtml = delta > 0
                ? `<span class="accrual-tag positive">+${formatUSD(delta)} accrued</span>`
                : '';

            const rangeData = calculateRangeData(pos);
            const rangeHtml = createRangeIndicator(rangeData);
            const rangeStatus = !rangeData ? null : (rangeData.inRange ? 'In range' : 'Out of range');
            const rangeStatusClass = !rangeData ? '' : (rangeData.inRange ? 'in-range' : 'out-of-range');

            // Format APRs
            const apr1d = pos.apr_1d ? (pos.apr_1d * 100).toFixed(2) + '%' : '0.00%';
            const apr7d = pos.apr_7d ? (pos.apr_7d * 100).toFixed(2) + '%' : '0.00%';

            // Determine APR Color Class
            const getAprClass = (val) => {
                const v = (val || 0) * 100;
                if (v > 20) return 'apr-high';
                if (v > 5) return 'apr-med';
                return 'apr-low';
            };


            const asset0 = pos.assets[0] || { symbol: '---', balance: 0, balanceUSD: 0 };
            const asset1 = pos.assets[1] || { symbol: '---', balance: 0, balanceUSD: 0 };

            // Backtest link params — mirrors the Pool Explorer result-table Backtest button.
            // No date selector on the LP page, so start/end are omitted and the backtester
            // falls back to its built-in default range (2y ago -> today).
            const btToken1 = (asset0.symbol || '').toLowerCase();
            const btToken2 = (asset1.symbol || '').toLowerCase();
            const btApr = ((pos.apr_7d || pos.apr_1d || 0) * 100).toFixed(2);
            const btHref = `/backtester?token1=${encodeURIComponent(btToken1)}&token2=${encodeURIComponent(btToken2)}&apr=${btApr}`;

            const unclaimed0 = (pos.unclaimed && pos.unclaimed[0]) || { balance: 0, balanceUSD: 0 };
            const unclaimed1 = (pos.unclaimed && pos.unclaimed[1]) || { balance: 0, balanceUSD: 0 };
            
            const claimed0 = (pos.claimed && pos.claimed[0]) || { balance: 0, balanceUSD: 0 };
            const claimed1 = (pos.claimed && pos.claimed[1]) || { balance: 0, balanceUSD: 0 };
            
            const totalAmt0 = Number(asset0.balance || 0) + Number(unclaimed0.balance || 0) + Number(claimed0.balance || 0);
            const totalAmt1 = Number(asset1.balance || 0) + Number(unclaimed1.balance || 0) + Number(claimed1.balance || 0);
            
            const pooledUSD = Number(asset0.balanceUSD || 0) + Number(asset1.balanceUSD || 0);
            const claimableUSD = Number(unclaimed0.balanceUSD || 0) + Number(unclaimed1.balanceUSD || 0);
            const claimedUSD = Number(claimed0.balanceUSD || 0) + Number(claimed1.balanceUSD || 0);
            const grandTotalUSD = pooledUSD + claimableUSD + claimedUSD;

            row.innerHTML = `
                <div class="lp-row-main">
                    ${renderPairPath(asset0, asset1, pos.images, pos.protocol, pos.range_data?.fee_tier, pos.apr_7d, pos.pool_address, pos.network, pos.defillama_uuid, pos.token_id)}
                    <div class="lp-row-meta pos-meta">
                        <span class="badge ${pos.network.toLowerCase()}">${pos.network}</span>
                        ${walletDisplay ? `<span class="wallet-tag" title="${walletAddr}">${walletDisplay}</span>` : ''}
                    </div>
                    <div class="lp-row-total">
                        <span class="lp-total-label">Total value</span>
                        <strong>${formatUSD(grandTotalUSD)}</strong>
                    </div>
                    <button type="button" class="expand-toggle" aria-label="Toggle position details" aria-expanded="false">
                        <svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4,6 8,10 12,6"/></svg>
                    </button>
                </div>

                <!-- DRAWER (Hidden by default) -->
                <div class="drawer" onclick="event.stopPropagation();">
                    <div class="drawer-section">
                        <div class="drawer-title">Overview</div>
                        <div class="pos-meta lp-drawer-meta">
                            <span class="badge ${pos.network.toLowerCase()}">${pos.network}</span>
                            ${walletDisplay ? `<span class="wallet-tag" title="${walletAddr}">${walletDisplay}</span>` : ''}
                            <span class="protocol-tag">${pos.protocol}${pos.token_id ? ` <span class="lp-token-id">#${pos.token_id}</span>` : ''}</span>
                            ${pos.isClosed ? '<span class="lp-closed-tag">Closed</span>' : ''}
                            ${accrualHtml}
                        </div>
                    </div>
                    <div class="drawer-section">
                        <div class="drawer-title">Range</div>
                        ${rangeStatus ? `<span class="lp-range-status ${rangeStatusClass}">${rangeStatus}</span>` : ''}
                        ${rangeHtml}
                    </div>
                    <div class="drawer-section">
                        <div class="drawer-title">Balances</div>
                        <div class="drawer-item"><span class="drawer-label">Pooled</span><span class="drawer-value">${formatUSD(pooledUSD)}</span></div>
                        <div class="drawer-item"><span class="drawer-label">Claimable</span><span class="drawer-value">${formatUSD(claimableUSD)}</span></div>
                        <div class="drawer-item"><span class="drawer-label">Claimed</span><span class="drawer-value">${formatUSD(claimedUSD)}</span></div>
                        <div class="drawer-item"><span class="drawer-label">Total</span><span class="drawer-value">${formatUSD(grandTotalUSD)}</span></div>
                    </div>
                    <div class="drawer-section">
                        <div class="drawer-title">Performance (APR)</div>
                        <div class="drawer-item">
                            <span class="drawer-label">24h APR</span>
                            <span class="drawer-value ${getAprClass(pos.apr_1d)}">${apr1d}</span>
                        </div>
                        <div class="drawer-item">
                            <span class="drawer-label">7d APR</span>
                            <span class="drawer-value ${getAprClass(pos.apr_7d)}">${apr7d}</span>
                        </div>

                        <button class="history-btn" onclick="openHistoryModal('${pos.position_key}', '${cleanedLabel.replace(/'/g, "\\'") + (pos.isClosed ? ' (Closed)' : '')}')">
                            <i class="fas fa-history"></i> View History
                        </button>

                        <a href="/pool?id=${pos.pool_id}" class="history-btn" style="text-decoration:none; margin-top:0.5rem; display:block; text-align:center;">
                            <i class="fas fa-chart-line"></i> Pool Analytics
                        </a>

                        <a href="${btHref}" class="history-btn" target="_blank" style="text-decoration:none; margin-top:0.5rem; display:block; text-align:center;" onclick="event.stopPropagation();">
                            <i class="fas fa-chart-area"></i> Backtest
                        </a>
                    </div>
                    <div class="drawer-section">
                        <div class="drawer-title">Invested Tokens</div>
                        ${pos.assets.filter(a => a.symbol).map(asset => `
                            <div class="drawer-item">
                                <span class="drawer-label">${asset.symbol}</span>
                                <div style="text-align:right">
                                    <div class="drawer-value">${formatTokenAmount(asset.balance)}</div>
                                    <div style="font-size:0.75rem; color:var(--text-secondary);">$${formatUSD(asset.balanceUSD)}</div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;

            const expandBtn = row.querySelector('.expand-toggle');
            const toggleDrawer = () => {
                const drawer = row.querySelector('.drawer');
                if (!drawer) return;
                const isOpen = drawer.classList.toggle('open');
                row.classList.toggle('active', isOpen);
                expandBtn.setAttribute('aria-expanded', String(isOpen));
            };

            expandBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleDrawer();
            });

            positionsGrid.appendChild(row);
        });
    };

    const calculateRangeData = (position) => {
        if (position.range_data && position.range_data.token_id) {
            const rd = position.range_data;
            // Ensure numeric types
            return {
                inRange: rd.in_range,
                minPrice: Number(rd.price_lower),
                maxPrice: Number(rd.price_upper),
                currentPrice: Number(rd.current_price)
            };
        }
        return null;
    };

    const createRangeIndicator = (rangeData) => {
        if (!rangeData) return '';
        const { inRange, minPrice, maxPrice, currentPrice } = rangeData;

        // Validations
        if (minPrice == null || maxPrice == null || currentPrice == null) return '';

        const fmt = (n) => {
            if (n === undefined || n === null || isNaN(n)) return '-';
            // Also apply int rounding rule for range labels? User said "each number on LP Dashboard"
            if (n > 100) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
            return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
        };

        // 1. Determine Visual Bounds (Prices)
        // We want the slider to cover [minPrice, maxPrice] AND currentPrice, plus some padding.

        let visualMin = Math.min(minPrice, currentPrice);
        let visualMax = Math.max(maxPrice, currentPrice);

        // Add padding (15%)
        const rangeSpan = visualMax - visualMin;
        const padding = rangeSpan * 0.15;

        // Ensure visual range isn't zero
        if (rangeSpan === 0) {
            visualMin = visualMin * 0.9;
            visualMax = visualMax * 1.1;
        } else {
            visualMin = Math.max(0, visualMin - padding); // Don't go below 0
            visualMax = visualMax + padding;
        }

        const totalSpan = visualMax - visualMin;
        if (totalSpan === 0) return ''; // Should not happen with padding

        // 3. Determine Status Color
        let statusColorClass = 'status-green'; // Default safe

        if (currentPrice < minPrice || currentPrice > maxPrice) {
            statusColorClass = 'status-red';
        } else {
            // Check proximity to edges within range (e.g., within 10% of edges)
            const rangeWidth = maxPrice - minPrice;
            const distToMin = (currentPrice - minPrice) / rangeWidth;
            const distToMax = (maxPrice - currentPrice) / rangeWidth;

            if (distToMin < 0.15 || distToMax < 0.15) {
                statusColorClass = 'status-yellow';
            }
        }

        // 2. Calculate Percentages for elements
        const getPct = (price) => ((price - visualMin) / totalSpan) * 100;

        const pctMin = Math.max(0, Math.min(100, getPct(minPrice)));
        const pctMax = Math.max(0, Math.min(100, getPct(maxPrice)));
        const pctCurr = Math.max(0, Math.min(100, getPct(currentPrice)));

        // Range Bar width
        const barLeft = Math.min(pctMin, pctMax);
        const barWidth = Math.abs(pctMax - pctMin);

        // Range segment color
        // Use dynamic check for in-range color
        const isInRange = (currentPrice >= Math.min(minPrice, maxPrice)) && (currentPrice <= Math.max(minPrice, maxPrice));
        let rangeClass = isInRange ? 'in-range' : 'out-range';
        if (statusColorClass === 'status-yellow') {
            rangeClass = 'warning';
        }

        return `
            <div class="range-indicator">
                <div class="range-bar-container wide-slider">
                    <!-- The full track is implied by the container width -->
                    
                    <!-- The Active Range Segment -->
                    <div class="range-segment ${rangeClass}" 
                         style="left: ${barLeft}%; width: ${barWidth}%;"></div>
                    
                    <!-- Min/Max Dots -->
                    <div class="range-dot min-dot" style="left: ${pctMin}%;"></div>
                    <div class="range-dot max-dot" style="left: ${pctMax}%;"></div>

                    <!-- Current Price Dot -->
                    <div class="range-current-price" style="left: ${pctCurr}%">
                        <span class="range-label-current ${statusColorClass}">${fmt(currentPrice)}</span>
                        <div class="current-dot ${statusColorClass}"></div>
                    </div>
                </div>
                <div class="range-labels">
                    <!-- Position labels based on calculated percents to align with markers -->
                    <span style="left: ${pctMin}%">Min: ${fmt(minPrice)}</span>
                    <span style="left: ${pctMax}%">Max: ${fmt(maxPrice)}</span>
                </div>
            </div>
        `;
    };

    const fetchLPSummary = async () => {
        loader.classList.remove('hidden');
        positionsGrid.innerHTML = '';
        noDataMsg.classList.add('hidden');

        try {
            const response = await fetch(`/api/lp/position-summary?t=${Date.now()}`);
            if (!response.ok) throw new Error('Failed to fetch LP summary');

            const data = await response.json();

            if (!data || data.length === 0) {
                noDataMsg.classList.remove('hidden');
                return;
            }

            // Calculate totals (from most recent snapshot of each position_label)
            const latestPositions = {};
            data.forEach(pos => {
                const key = `${pos.protocol}-${pos.position_label}-${pos.network}`;
                if (!latestPositions[key] || new Date(pos.timestamp) > new Date(latestPositions[key].timestamp)) {
                    latestPositions[key] = pos;
                }
            });

            // Detect closed positions and Filter valid LP positions
            const BLOCKED_PROTOCOLS = ['Aave Safety Module', 'Lido', 'Sky', 'Aave V3'];

            const uniqueRaw = Object.values(latestPositions);
            if (uniqueRaw.length > 0) {
                const maxTs = Math.max(...uniqueRaw.map(p => new Date(p.timestamp).getTime()));
                allPositions = uniqueRaw
                    .filter(p => !BLOCKED_PROTOCOLS.includes(p.protocol)) // Filter blocked protocols
                    .filter(p => p.assets && p.assets.length >= 2) // Filter: Must have at least 2 assets (LP)
                    .map(p => {
                        const pTs = new Date(p.timestamp).getTime();
                        p.isClosed = (maxTs - pTs) > (48 * 60 * 60 * 1000); // 48h threshold
                        return p;
                    });
            } else {
                allPositions = [];
            }

            const totalValue = allPositions.reduce((sum, p) => sum + p.balance_usd, 0);
            const totalRewards = allPositions.reduce((sum, p) => sum + p.total_unclaimed_usd, 0);

            totalPortfolioValueEl.textContent = formatUSD(totalValue);
            totalRewardsEl.textContent = formatUSD(totalRewards);

            // Populate filters
            const protocols = [...new Set(allPositions.map(p => p.protocol))].sort();
            protocolFilter.innerHTML = '<option value="all">All Protocols</option>' +
                protocols.map(p => `<option value="${p}">${p}</option>`).join('');

            const wallets = [...new Set(allPositions.map(p => p.address).filter(a => a))].sort();
            walletFilter.innerHTML = '<option value="all">All Wallets</option>' +
                wallets.map(w => {
                    const disp = w.length > 6 ? `...${w.slice(-4)}` : w;
                    return `<option value="${w}">${disp}</option>`;
                }).join('');

            // Apply initial filters and sorting
            applyFiltersAndSort();

        } catch (error) {
            console.error('Error fetching LP summary:', error);
            noDataMsg.textContent = `Error: ${error.message}`;
            noDataMsg.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    };

    // Event listeners for filters and sorting
    if (networkFilter) {
        networkFilter.addEventListener('change', (e) => {
            currentFilters.network = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (protocolFilter) {
        protocolFilter.addEventListener('change', (e) => {
            currentFilters.protocol = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (walletFilter) {
        walletFilter.addEventListener('change', (e) => {
            currentFilters.wallet = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (searchFilter) {
        searchFilter.addEventListener('input', (e) => {
            currentFilters.search = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (sortBy) {
        sortBy.addEventListener('change', (e) => {
            currentSort = e.target.value;
            applyFiltersAndSort();
        });
    }

    const hideClosedCheckbox = document.getElementById('hide-closed-filter');
    if (hideClosedCheckbox) {
        hideClosedCheckbox.addEventListener('change', () => {
            applyFiltersAndSort();
        });
    }

    fetchLPSummary();
});

/* Force reload */
