/* oryxflow docs — make the "oryxflow" wordmark clickable.

   Material renders the site name twice as plain, dead text: in the black top
   banner and as the sidebar heading above the nav. Both sit next to a logo that
   *is* a link home, so readers try to click the word and nothing happens. This
   wraps that text in the same home link.

   Done in script rather than by copying Material's header.html / nav.html into
   overrides/: those partials carry the search bar, palette toggle, repo widget
   and tab logic, and a vendored copy silently goes stale on every theme upgrade.
   Only the class names are relied on here, and they are part of Material's
   public CSS surface. The logo link beside each title already gives crawlers the
   home link, so nothing is lost if this never runs. */

(function () {
  var CLASS = "of-home-link";

  /* Wrap the element's own text (the site name) in a link to `href`.
     Skips nested elements — in the sidebar the label also contains the logo
     anchor, which must be left alone. */
  function linkifyText(el, href) {
    if (!el || el.querySelector("a." + CLASS)) return;
    for (var i = 0; i < el.childNodes.length; i++) {
      var node = el.childNodes[i];
      if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim()) continue;
      var link = document.createElement("a");
      link.className = CLASS;
      link.href = href;
      link.textContent = node.textContent.trim();
      el.replaceChild(link, node);
      return;
    }
  }

  function linkifyTitles() {
    /* Take the target from the logo link so it stays correct at any URL depth
       (and honours a custom extra.homepage). */
    var logo = document.querySelector(".md-header a.md-logo, .md-nav a.md-logo");
    if (!logo) return;
    var href = logo.getAttribute("href");

    linkifyText(
      document.querySelector(
        ".md-header__title .md-header__topic:first-child .md-ellipsis"
      ),
      href
    );
    linkifyText(document.querySelector(".md-nav--primary > .md-nav__title"), href);
  }

  /* document$ re-fires on every instant-loading page swap; without it the links
     would only exist on the first page loaded. */
  if (typeof document$ !== "undefined") {
    document$.subscribe(linkifyTitles);
  } else {
    document.addEventListener("DOMContentLoaded", linkifyTitles);
  }
})();
