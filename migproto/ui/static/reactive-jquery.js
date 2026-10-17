/* Progressive jQuery + Bootstrap enhancement for the evidence-first dashboard. */
(function (jq) {
  "use strict";
  if (!jq) return;

  const $j = jq;
  let tooltipInstances = [];

  function animateNumber(element) {
    const $element = $j(element);
    if ($element.data("counted")) return;
    const raw = $element.text().replace(/,/g, "").trim();
    const target = Number(raw);
    if (!Number.isFinite(target)) return;
    $element.data("counted", true);
    $j({value: 0}).animate({value: target}, {
      duration: 520,
      easing: "swing",
      step: function () { $element.text(Math.round(this.value).toLocaleString()); },
      complete: function () { $element.text(target.toLocaleString()); }
    });
  }

  function syncVisibleReviews() {
    const $rows = $j(".run-row");
    if (!$rows.length) return;
    const visible = $rows.filter(function () { return !this.hidden; }).length;
    let $count = $j(".library-panel .result-count");
    if (!$count.length) {
      $count = $j('<span class="result-count badge text-bg-light"></span>');
      $j(".library-panel>header>div:first").append($count);
    }
    $count.text(`${visible} review${visible === 1 ? "" : "s"}`);
  }

  function hydrate(root) {
    const $root = $j(root || document);
    $root.find("button:not(.mark):not(.compat-result):not(.workspace-stage):not(.icon-button):not(.tour-close):not(.drawer-close)").addClass("btn btn-sm");
    $root.find(".button.primary").addClass("btn btn-dark");
    $root.find(".button.ghost").addClass("btn btn-outline-secondary");
    $root.find("select").addClass("form-select form-select-sm");
    $root.find("table").addClass("table table-hover align-middle mb-0");
    $root.find(".attempt-switch").addClass("btn-group").attr("role", "group");
    $root.find(".scope-filters").addClass("row g-2").children("select").addClass("col");
    $root.find(".kpi-ribbon strong").each(function () { animateNumber(this); });
    $root.find(".dashboard-title,.kpi-ribbon,.command-grid,.library-panel").addClass("ui-enter");
    syncVisibleReviews();

    if (window.bootstrap?.Tooltip) {
      tooltipInstances.forEach((instance) => instance.dispose());
      tooltipInstances = $root.find('[title]:not([disabled])').toArray().map((node) => new window.bootstrap.Tooltip(node, {container: "body", trigger: "hover focus"}));
    }
  }

  $j(document).on("input change", "#run-search,#verdict-filter,#kind-filter", function () {
    window.requestAnimationFrame(syncVisibleReviews);
  });

  $j(document).on("click", ".compat-result:not(:disabled)", function () {
    $j(this).addClass("reactive-selected");
    window.setTimeout(() => $j(this).removeClass("reactive-selected"), 500);
  });

  $j(document).on("click", ".workspace-stage", function () {
    $j(".workspace-stage").removeClass("reactive-active");
    $j(this).addClass("reactive-active");
  });

  const view = document.getElementById("view");
  if (view) new MutationObserver(() => hydrate(view)).observe(view, {childList: true});
  $j(function () {
    document.documentElement.dataset.uiFramework = "jquery-bootstrap";
    hydrate(document);
  });
})(window.jQuery);
