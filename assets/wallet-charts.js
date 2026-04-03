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
    var areaOptions = {
      lineColor: "#0093fd",
      topColor: "rgba(0, 147, 253, 0.25)",
      bottomColor: "rgba(155, 81, 224, 0.005)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    };

    if (typeof chart.addSeries === "function" && window.LightweightCharts.AreaSeries) {
      return chart.addSeries(window.LightweightCharts.AreaSeries, areaOptions);
    }
    return chart.addAreaSeries(areaOptions);
  }

  function createChartEntry(containerId) {
    const container = document.getElementById(containerId);
    if (!container || !window.LightweightCharts) {
      return null;
    }

    const chart = window.LightweightCharts.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "#181d21" },
        textColor: "#7b8996",
        fontFamily: '"Inter", "Segoe UI", sans-serif',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.025)" },
        horzLines: { color: "rgba(255, 255, 255, 0.025)" },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.14, bottom: 0.1 },
      },
      leftPriceScale: { visible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
        barSpacing: 14,
      },
      crosshair: {
        vertLine: {
          color: "rgba(151, 163, 183, 0.22)",
          labelBackgroundColor: "#0093fd",
        },
        horzLine: {
          color: "rgba(151, 163, 183, 0.22)",
          labelBackgroundColor: "#0093fd",
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
        color: "rgba(255, 255, 255, 0.14)",
        fontSize: 13,
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
