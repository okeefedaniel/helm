/* ============================================
   Helm — Executive Dashboard JavaScript
   Chart.js sparklines + htmx configuration
   ============================================ */

document.addEventListener('DOMContentLoaded', function () {
    initSparklines();
});

/* Re-init sparklines after htmx swaps new content */
document.addEventListener('htmx:afterSwap', function () {
    initSparklines();
});

/**
 * Initialize all sparkline mini-charts on the page.
 * Each <canvas class="sparkline-chart"> has:
 *   data-values="42,43,44,45,..."
 *   data-labels="Apr,May,Jun,..."
 */
function initSparklines() {
    document.querySelectorAll('.sparkline-chart').forEach(function (canvas) {
        /* Skip if already initialized */
        if (canvas.dataset.initialized === 'true') return;

        var rawValues = canvas.dataset.values || '';
        var rawLabels = canvas.dataset.labels || '';

        if (!rawValues) return;

        var values = rawValues.split(',').map(Number);
        var labels = rawLabels.split(',');

        new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    borderColor: '#1F64E5',
                    backgroundColor: 'rgba(31, 100, 229, 0.08)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 3,
                    pointHoverBackgroundColor: '#1F64E5',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: true,
                        mode: 'index',
                        intersect: false,
                        displayColors: false,
                        padding: 6,
                        titleFont: { size: 10 },
                        bodyFont: { size: 11 },
                    }
                },
                scales: {
                    x: { display: false },
                    y: { display: false },
                },
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                elements: {
                    line: { borderCapStyle: 'round' }
                }
            }
        });

        canvas.dataset.initialized = 'true';
    });
}
