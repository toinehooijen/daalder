(function () {
  "use strict";

  var prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)"
  ).matches;

  function formatEuro(value) {
    return (
      "€ " +
      value.toLocaleString("nl-NL", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function initPriceCard() {
    var card = document.querySelector("[data-price-card]");
    if (!card) return;

    var nowEl = card.querySelector('[data-role="now"]');
    var savedEl = card.querySelector('[data-role="saved"]');
    var start = parseFloat(card.dataset.start);
    var end = parseFloat(card.dataset.end);
    if (!nowEl || isNaN(start) || isNaN(end)) return;

    // Static HTML already shows the settled, correct end state — this is
    // the no-JS / reduced-motion baseline. Only rewind + animate if motion
    // is allowed.
    if (prefersReducedMotion) return;

    savedEl.classList.remove("is-visible");
    nowEl.textContent = formatEuro(start);

    var duration = 1200;
    var startTime = null;

    function tick(timestamp) {
      if (startTime === null) startTime = timestamp;
      var elapsed = timestamp - startTime;
      var progress = Math.min(elapsed / duration, 1);
      var eased = easeOutCubic(progress);
      var value = start - (start - end) * eased;
      nowEl.textContent = formatEuro(value);

      if (progress < 1) {
        window.requestAnimationFrame(tick);
      } else {
        nowEl.textContent = formatEuro(end);
        savedEl.classList.add("is-visible");
      }
    }

    // small delay so the animation reads as a deliberate moment, not a
    // layout flash, once the page has settled.
    window.setTimeout(function () {
      window.requestAnimationFrame(tick);
    }, 350);
  }

  function initStickyCta() {
    var heroCta = document.getElementById("hero-cta");
    var stickyBar = document.getElementById("sticky-cta");
    if (!heroCta || !stickyBar || !("IntersectionObserver" in window)) return;

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          stickyBar.classList.toggle("is-visible", !entry.isIntersecting);
        });
      },
      { rootMargin: "0px 0px -10% 0px" }
    );
    observer.observe(heroCta);
  }

  function initFaqChevrons() {
    document.querySelectorAll(".faq-item").forEach(function (item) {
      item.addEventListener("toggle", function () {
        var chevron = item.querySelector(".faq-chevron");
        if (chevron) chevron.classList.toggle("is-open", item.open);
      });
    });
  }

  function initFooterYear() {
    var yearEl = document.getElementById("year");
    if (yearEl) yearEl.textContent = new Date().getFullYear();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initPriceCard();
    initStickyCta();
    initFaqChevrons();
    initFooterYear();
  });
})();
