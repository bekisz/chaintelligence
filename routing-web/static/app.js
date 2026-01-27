document.addEventListener('DOMContentLoaded', () => {
    const analyzeBtn = document.getElementById('analyze-btn');
    const startTokenInput = document.getElementById('start-token');
    const endTokenInput = document.getElementById('end-token');
    const daysInput = document.getElementById('days');
    const resultsSection = document.getElementById('results-section');
    const totalVolumeEl = document.getElementById('total-volume');
    const totalTxEl = document.getElementById('total-tx');
    const routesBody = document.getElementById('routes-body');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');

    const formatUSD = (amount) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2
        }).format(amount);
    };

    const performAnalysis = async () => {
        const startToken = startTokenInput.value.trim().toUpperCase();
        const endToken = endTokenInput.value.trim().toUpperCase();
        const days = parseFloat(daysInput.value);

        if (!startToken || !endToken) {
            alert('Please enter both start and end tokens.');
            return;
        }

        // Show loader, hide results
        loader.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');

        try {
            const response = await fetch(`/api/analyze?start_token=${startToken}&end_token=${endToken}&days=${days}`);
            if (!response.ok) {
                throw new Error('API request failed');
            }

            const data = await response.json();

            if (!data.routes || data.routes.length === 0) {
                noDataMsg.classList.remove('hidden');
                loader.classList.add('hidden');
                return;
            }

            // Update stats
            totalVolumeEl.textContent = formatUSD(data.total_volume);
            totalTxEl.textContent = data.total_tx.toLocaleString();

            // Render table
            routesBody.innerHTML = '';
            data.routes.forEach(route => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td class="path-cell">${route.path}</td>
                    <td>${route.count.toLocaleString()}</td>
                    <td class="font-bold">${formatUSD(route.volume)}</td>
                    <td>${formatUSD(route.avg_volume)}</td>
                    <td class="accent-text">${route.pct_volume.toFixed(1)}%</td>
                `;
                routesBody.appendChild(row);
            });

            // Show results
            resultsSection.classList.remove('hidden');
        } catch (error) {
            console.error('Error during analysis:', error);
            alert('Analysis failed. Please check the console for details.');
        } finally {
            loader.classList.add('hidden');
        }
    };

    analyzeBtn.addEventListener('click', performAnalysis);

    // Allow Enter key to trigger analysis
    [startTokenInput, endTokenInput, daysInput].forEach(input => {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performAnalysis();
            }
        });
    });
});
