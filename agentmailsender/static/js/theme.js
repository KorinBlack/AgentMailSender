// Applies the saved theme before first paint (a classic script, so it runs synchronously).
(function () {
  try {
    var theme = localStorage.getItem("ams-theme");
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  } catch (e) { /* storage unavailable: follow the system */ }
})();
