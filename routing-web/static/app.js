document.addEventListener('DOMContentLoaded', async () => {
    const analyzeBtn = document.getElementById('analyze-btn');
    const startTokenInput = document.getElementById('start-token');
    const endTokenInput = document.getElementById('end-token');
    const resultsSection = document.getElementById('results-section');
    const totalVolumeEl = document.getElementById('total-volume');
    const totalTxEl = document.getElementById('total-tx');
    const routesBody = document.getElementById('routes-body');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');
    const startDateInput = document.getElementById('start-date');
    const endDateInput = document.getElementById('end-date');

    // Fetch available date range from API and set defaults
    try {
        const response = await fetch('/api/date-range');
        const dateRange = await response.json();

        if (dateRange.min_date && dateRange.max_date) {
            // Set min/max constraints on date inputs
            startDateInput.min = dateRange.min_date;
            startDateInput.max = dateRange.max_date;
            endDateInput.min = dateRange.min_date;
            endDateInput.max = dateRange.max_date;

            // Set default end date to today (or max_date if today is beyond available data)
            const today = new Date().toISOString().split('T')[0];
            const maxDate = dateRange.max_date;
            endDateInput.value = today <= maxDate ? today : maxDate;

            // Set default start date to 30 days before end date (or min_date if less than 30 days available)
            const endDate = new Date(endDateInput.value);
            const thirtyDaysAgo = new Date(endDate);
            thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
            const thirtyDaysAgoStr = thirtyDaysAgo.toISOString().split('T')[0];

            startDateInput.value = thirtyDaysAgoStr >= dateRange.min_date ? thirtyDaysAgoStr : dateRange.min_date;
        }
    } catch (error) {
        console.error('Error fetching date range:', error);
        // Fallback: just set end date to today
        const today = new Date().toISOString().split('T')[0];
        endDateInput.value = today;
    }

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
        const startDate = startDateInput.value;
        const endDate = endDateInput.value;

        if (!startToken || !endToken) {
            alert('Please enter both start and end tokens.');
            return;
        }

        // Show loader, hide results
        loader.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');

        try {
            let url = `/api/analyze?start_token=${startToken}&end_token=${endToken}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;

            const response = await fetch(url);
            if (!response.ok) {
                throw new Error('API request failed');
            }

            const data = await response.json();

            if (!data.routes || data.routes.length === 0) {
                let msg = 'No swap data found for the specified period and tokens.';
                if (data.db_range) {
                    msg += `<br/><small>Data available in DB from ${data.db_range.min} to ${data.db_range.max}</small>`;
                }
                noDataMsg.innerHTML = `<p>${msg}</p>`;
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
    [startTokenInput, endTokenInput, startDateInput, endDateInput].forEach(input => {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performAnalysis();
            }
        });
    });
});
