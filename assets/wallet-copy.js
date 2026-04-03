/**
 * Click-to-copy for wallet addresses.
 * Click any element with data-clipboard to copy its value.
 */
document.addEventListener("click", function (e) {
  var el = e.target.closest("[data-clipboard]");
  if (!el) return;

  var text = el.getAttribute("data-clipboard");
  if (!text) return;

  navigator.clipboard.writeText(text).then(function () {
    var original = el.textContent;
    el.textContent = "Copied!";
    el.style.color = "#22c55e";
    setTimeout(function () {
      el.textContent = original;
      el.style.color = "";
    }, 1200);
  });
});
