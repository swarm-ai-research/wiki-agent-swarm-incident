/* Injected archive nav. Resolves relative to this script so project Pages
   at /wiki-agent-swarm-incident/ and local file:// copies both work. */
(function () {
  var script = document.currentScript;
  var base = "";
  if (script && script.src) {
    base = script.src.replace(/site-nav\.js(?:\?.*)?$/, "");
  }
  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }
  var path = (location.pathname || "").replace(/\/+$/, "").toLowerCase();
  var leaf = path.split("/").pop() || "index.html";
  if (leaf === "" || leaf === "wiki-agent-swarm-incident") leaf = "index.html";
  var inAnalysis = /\/analysis\//.test(path) || /(^|\/)analysis$/.test(path);
  var inSources = leaf === "sources.md" || leaf === "sources.html";
  var inNotes = inAnalysis && leaf.indexOf("timeline") === 0;

  var items = [
    { href: "index.html", current: leaf === "index.html" && !inAnalysis, label: "Summary" },
    { href: "timeline.html", current: leaf === "timeline.html" && !inAnalysis, label: "Timeline" },
    { href: "graph.html", current: leaf === "graph.html", label: "Graph" },
    { href: "report.html", current: leaf === "report.html", label: "Report" },
    { href: "analysis/timeline.html", current: inNotes, label: "Notes" },
    { href: "sources.md", current: inSources, label: "Sources" }
  ];

  ready(function () {
  if (!document.body) return;
  var nav = document.createElement("nav");
  nav.className = "archive-nav";
  nav.setAttribute("aria-label", "Archive");
  var inner = document.createElement("div");
  inner.className = "inner";
  inner.innerHTML = '<a class="brand" href="' + base + 'index.html">SWARM · <b>Wiki swarm archive</b><span> · 2026</span></a>';
  var links = document.createElement("div");
  links.className = "links";
  items.forEach(function (item) {
    var a = document.createElement("a");
    a.className = "item";
    a.href = base + item.href;
    a.textContent = item.label;
    if (item.current) a.setAttribute("aria-current", "page");
    links.appendChild(a);
  });
  var repo = document.createElement("a");
  repo.className = "item ext";
  repo.href = "https://github.com/swarm-ai-research/wiki-agent-swarm-incident";
  repo.textContent = "Repo";
  links.appendChild(repo);
  inner.appendChild(links);
  nav.appendChild(inner);
  document.body.insertBefore(nav, document.body.firstChild);
  document.body.classList.add("has-archive-nav");
  });
})();
