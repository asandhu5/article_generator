(() => {
  const markdownText = window.__MARKDOWN__ || "";
  const proseEl = document.getElementById("article-prose");
  const tocList = document.getElementById("toc-list");

  if (!proseEl || !window.marked) return;

  proseEl.innerHTML = marked.parse(markdownText);

  // Build a table of contents from H2s (the title itself is the lone H1,
  // already shown in the page header, so it isn't repeated here).
  const headings = Array.from(proseEl.querySelectorAll("h2"));
  headings.forEach((h, idx) => {
    const id = `section-${idx}`;
    h.id = id;
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `#${id}`;
    a.textContent = h.textContent;
    li.appendChild(a);
    tocList.appendChild(li);
  });

  // Scroll-spy: highlight the TOC entry for whichever heading is nearest
  // the top of the viewport.
  const tocLinks = Array.from(tocList.querySelectorAll("a"));
  function updateActiveLink() {
    let activeIndex = 0;
    headings.forEach((h, idx) => {
      if (h.getBoundingClientRect().top <= 120) activeIndex = idx;
    });
    tocLinks.forEach((a, idx) => a.classList.toggle("active", idx === activeIndex));
  }
  if (headings.length) {
    window.addEventListener("scroll", updateActiveLink, { passive: true });
    updateActiveLink();
  }
})();
