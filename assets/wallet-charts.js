(function () {
  const chartRegistry = new Map();

  function formatMoney(value) {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return "-";
    }
    const sign = value < 0 ? "-" : "";
    return sign + "$" + Math.abs(value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function createSeries(chart) {
    const baselineOptions = {
      baseValue: { type: "price", price: 0 },
      topLineColor: "#23c26b",
      topFillColor1: "rgba(35, 194, 107, 0.35)",
      topFillColor2: "rgba(35, 194, 107, 0.05)",
      bottomLineColor: "#ff5b5b",
      bottomFillColor1: "rgba(255, 91, 91, 0.22)",
      bottomFillColor2: "rgba(255, 91, 91, 0.04)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    };

    if (typeof chart.addSeries === "function" && window.LightweightCharts.BaselineSeries) {
      return chart.addSeries(window.LightweightCharts.BaselineSeries, baselineOptions);
    }
    if (typeof chart.addBaselineSeries === "function") {
      return chart.addBaselineSeries(baselineOptions);
    }
    if (typeof chart.addSeries === "function" && window.LightweightCharts.AreaSeries) {
      return chart.addSeries(window.LightweightCharts.AreaSeries, {
        lineColor: "#60a5fa",
        topColor: "rgba(96, 165, 250, 0.24)",
        bottomColor: "rgba(96, 165, 250, 0.03)",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
    }
    return chart.addAreaSeries({
      lineColor: "#60a5fa",
      topColor: "rgba(96, 165, 250, 0.24)",
      bottomColor: "rgba(96, 165, 250, 0.03)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
  }

  function createChartEntry(containerId) {
    const container = document.getElementById(containerId);
    if (!container || !window.LightweightCharts) {
      return null;
    }

    const chart = window.LightweightCharts.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "#1a1a1a" },
        textColor: "#9ca3af",
        fontFamily: '"Manrope", "Avenir Next", "Segoe UI", sans-serif',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.06)" },
        horzLines: { color: "rgba(255, 255, 255, 0.06)" },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.18, bottom: 0.12 },
      },
      leftPriceScale: { visible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
      },
      crosshair: {
        vertLine: {
          color: "rgba(148, 163, 184, 0.3)",
          labelBackgroundColor: "#2563eb",
        },
        horzLine: {
          color: "rgba(148, 163, 184, 0.3)",
          labelBackgroundColor: "#2563eb",
        },
      },
      handleScale: {
        mouseWheel: false,
        pinch: true,
        axisPressedMouseMove: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      localization: {
        priceFormatter: formatMoney,
      },
    });

    const series = createSeries(chart);
    const resizeObserver = new ResizeObserver(function (entries) {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const width = Math.floor(entry.contentRect.width);
      const height = Math.floor(entry.contentRect.height);
      if (width > 0 && height > 0) {
        chart.resize(width, height);
      }
    });
    resizeObserver.observe(container);

    const created = { chart: chart, series: series, resizeObserver: resizeObserver };
    chartRegistry.set(containerId, created);
    return created;
  }

  function ensureChart(containerId) {
    return chartRegistry.get(containerId) || createChartEntry(containerId);
  }

  function renderChart(containerId, payload) {
    const chartEntry = ensureChart(containerId);
    if (!chartEntry) {
      return;
    }

    const data = payload && Array.isArray(payload.series)
      ? payload.series.map(function (point) {
          return { time: point.time, value: point.value };
        })
      : [];

    chartEntry.series.setData(data);
    chartEntry.chart.applyOptions({
      watermark: {
        visible: data.length === 0,
        text: data.length === 0 ? "No history yet" : "",
        color: "rgba(255, 255, 255, 0.18)",
        fontSize: 14,
        horzAlign: "center",
        vertAlign: "center",
      },
    });

    if (data.length > 0) {
      chartEntry.chart.timeScale().fitContent();
    }
  }

  window.walletCuratorCharts = {
    renderChart: renderChart,
  };
}());
