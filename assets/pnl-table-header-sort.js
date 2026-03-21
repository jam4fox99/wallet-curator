(function () {
  function handleHeaderClick(event) {
    var header = event.target.closest('#daily-table th[data-dash-column]');
    if (!header) {
      return;
    }

    if (event.target.closest('.column-header--sort')) {
      return;
    }

    var sortControl = header.querySelector('.column-header--sort');
    if (!sortControl) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    sortControl.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      view: window
    }));
  }

  document.addEventListener('click', handleHeaderClick, true);
}());
