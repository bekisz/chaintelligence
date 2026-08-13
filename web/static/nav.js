// Unified Navigation Handler
document.addEventListener('DOMContentLoaded', () => {
    // Set active nav link based on current page
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.nav-link');

    navLinks.forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
            link.classList.add('active');
        }
    });
});

// Global copy helper used by inline onclick handlers across pages (route hashes,
// pool/address IDs, etc.). Defined here (shared nav.js) so it exists on every
// page, not just the ones that load pool.js.
window.copyToClipboard = (text, el, ev) => {
    if (ev) ev.stopPropagation();
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            const iconWrapper = el.querySelector('.copy-icon-wrapper');
            if (iconWrapper) {
                const orig = iconWrapper.innerHTML;
                iconWrapper.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
                setTimeout(() => { iconWrapper.innerHTML = orig; }, 1200);
            }
        }).catch(err => console.error('Copy error:', err));
    } else {
        // Fallback for non-secure contexts / older browsers.
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) { console.error('Copy error:', e); }
        document.body.removeChild(ta);
    }
};
