/* PIN visibility is strictly UI state, never persisted or transmitted. */
(() => {
  "use strict";
  const sync = (form, visible) => {
    form.querySelectorAll("[data-pin-secret]").forEach((input) => {
      input.type = visible ? "text" : "password";
    });
    form.querySelectorAll("[data-pin-toggle]").forEach((button) => {
      button.setAttribute("aria-pressed", String(visible));
      button.setAttribute("aria-label", visible ? "Скрыть PIN" : "Показать PIN");
      button.title = visible ? "Скрыть PIN" : "Показать PIN";
    });
  };
  document.querySelectorAll("[data-pin-visibility-root]").forEach((form) => {
    form.querySelectorAll("[data-pin-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const isVisible = button.getAttribute("aria-pressed") === "true";
        sync(form, !isVisible);
      });
    });
  });
})();
