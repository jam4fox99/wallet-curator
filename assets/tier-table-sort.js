/**
 * Client-side sort for tier tables.
 * Click a <th> with data-sortable to sort that column.
 * Numeric columns (data-sort-type="number") sort numerically.
 */
document.addEventListener("click", function (e) {
  var th = e.target.closest("th[data-sortable]");
  if (!th) return;

  var table = th.closest("table");
  if (!table) return;

  var colIndex = Array.from(th.parentNode.children).indexOf(th);
  var tbody = table.querySelector("tbody");
  if (!tbody) return;

  var rows = Array.from(tbody.querySelectorAll("tr"));
  var isNumeric = th.getAttribute("data-sort-type") === "number";

  // Toggle direction
  var currentDir = th.getAttribute("data-sort-dir") || "none";
  var newDir = currentDir === "asc" ? "desc" : "asc";

  // Clear all sort indicators in this table
  table.querySelectorAll("th[data-sortable]").forEach(function (h) {
    h.setAttribute("data-sort-dir", "none");
    h.classList.remove("pm-sort-asc", "pm-sort-desc");
  });

  th.setAttribute("data-sort-dir", newDir);
  th.classList.add(newDir === "asc" ? "pm-sort-asc" : "pm-sort-desc");

  rows.sort(function (a, b) {
    var aText = a.children[colIndex] ? a.children[colIndex].textContent.trim() : "";
    var bText = b.children[colIndex] ? b.children[colIndex].textContent.trim() : "";

    if (isNumeric) {
      var aNum = parseFloat(aText.replace(/[$,—]/g, "")) || 0;
      var bNum = parseFloat(bText.replace(/[$,—]/g, "")) || 0;
      return newDir === "asc" ? aNum - bNum : bNum - aNum;
    }

    return newDir === "asc"
      ? aText.localeCompare(bText)
      : bText.localeCompare(aText);
  });

  rows.forEach(function (row) {
    tbody.appendChild(row);
  });
});
