(function () {
  "use strict";

  const state = {
    papers: [],
    topics: [],
    activeTopic: "all",
    query: "",
  };

  const els = {
    papers: document.getElementById("papers"),
    topics: document.getElementById("topics"),
    search: document.getElementById("search"),
    updated: document.getElementById("updated"),
    stats: document.getElementById("stats"),
    empty: document.getElementById("empty"),
  };

  function showSkeletons() {
    els.papers.innerHTML = Array.from({ length: 5 })
      .map(() => '<div class="skeleton"></div>')
      .join("");
  }

  function timeAgo(iso) {
    const then = new Date(iso);
    const diffMs = Date.now() - then.getTime();
    const days = Math.floor(diffMs / 86400000);
    if (days <= 0) return "今天";
    if (days === 1) return "昨天";
    if (days < 30) return days + " 天前";
    const months = Math.floor(days / 30);
    return months + " 个月前";
  }

  function fmtDate(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function renderTopics() {
    const all = { name: "all", count: state.papers.length };
    const chips = [all, ...state.topics]
      .map((t) => {
        const label = t.name === "all" ? "全部" : escapeHtml(t.name);
        const active = state.activeTopic === t.name ? " active" : "";
        return (
          '<button class="chip' + active + '" data-topic="' +
          escapeHtml(t.name) + '">' + label +
          '<span class="cnt">' + t.count + "</span></button>"
        );
      })
      .join("");
    els.topics.innerHTML = chips;
    els.topics.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.activeTopic = chip.dataset.topic;
        renderTopics();
        renderPapers();
      });
    });
  }

  function matches(paper) {
    if (state.activeTopic !== "all" &&
        !paper.topics.includes(state.activeTopic)) {
      return false;
    }
    const q = state.query.trim().toLowerCase();
    if (!q) return true;
    const hay = (
      paper.title + " " + paper.summary + " " + paper.authors.join(" ")
    ).toLowerCase();
    return q.split(/\s+/).every((term) => hay.includes(term));
  }

  function paperCard(p) {
    const authors =
      p.authors.length > 6
        ? p.authors.slice(0, 6).join(", ") + " 等 " + p.authors.length + " 人"
        : p.authors.join(", ");

    const isNew = p.age_days <= 2;
    const primaryTag =
      '<span class="tag">' + escapeHtml(p.primary_topic) + "</span>";
    const otherTags = p.topics
      .filter((t) => t !== p.primary_topic)
      .map((t) => '<span class="tag sub">' + escapeHtml(t) + "</span>")
      .join("");
    const cats = (p.categories || [])
      .slice(0, 2)
      .map((c) => '<span class="tag sub">' + escapeHtml(c) + "</span>")
      .join("");

    return (
      '<article class="card">' +
        '<div class="card-top">' +
          "<h2><a href=\"" + escapeHtml(p.abs_url) +
            "\" target=\"_blank\" rel=\"noopener\">" +
            escapeHtml(p.title) + "</a></h2>" +
          '<span class="date">' + fmtDate(p.published) +
            (isNew ? ' <span class="new">NEW</span>' : "") +
          "</span>" +
        "</div>" +
        '<p class="authors">' + escapeHtml(authors) + "</p>" +
        '<p class="summary">' + escapeHtml(p.summary) + "</p>" +
        '<button class="toggle-sum">展开摘要 ▾</button>' +
        '<div class="card-tags">' +
          primaryTag + otherTags + cats +
          '<span class="card-links">' +
            "<a href=\"" + escapeHtml(p.abs_url) +
              "\" target=\"_blank\" rel=\"noopener\">arXiv</a>" +
            (p.pdf_url
              ? "<a href=\"" + escapeHtml(p.pdf_url) +
                "\" target=\"_blank\" rel=\"noopener\">PDF</a>"
              : "") +
          "</span>" +
        "</div>" +
      "</article>"
    );
  }

  function renderPapers() {
    const list = state.papers.filter(matches);
    if (!list.length) {
      els.papers.innerHTML = "";
      els.empty.classList.remove("hidden");
    } else {
      els.empty.classList.add("hidden");
      els.papers.innerHTML = list.map(paperCard).join("");
      wireCardToggles();
    }
    els.stats.textContent =
      "显示 " + list.length + " / " + state.papers.length + " 篇";
  }

  function wireCardToggles() {
    els.papers.querySelectorAll(".toggle-sum").forEach((btn) => {
      const summary = btn.previousElementSibling;
      btn.addEventListener("click", () => {
        const expanded = summary.classList.toggle("expanded");
        btn.textContent = expanded ? "收起摘要 ▴" : "展开摘要 ▾";
      });
    });
  }

  let searchTimer = null;
  els.search.addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const val = e.target.value;
    searchTimer = setTimeout(() => {
      state.query = val;
      renderPapers();
    }, 120);
  });

  async function load() {
    showSkeletons();
    try {
      const res = await fetch("data/papers.json?_=" + Date.now());
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      state.papers = data.papers || [];
      state.topics = data.topics || [];
      els.updated.textContent =
        "更新于 " + fmtDate(data.generated_at) +
        " · " + timeAgo(data.generated_at);
      renderTopics();
      renderPapers();
    } catch (err) {
      els.papers.innerHTML = "";
      els.empty.classList.remove("hidden");
      els.empty.innerHTML =
        "<p>无法加载论文数据（" + escapeHtml(String(err.message)) +
        "）。<br/>请先运行 <code>python3 fetch_papers.py</code> 生成 " +
        "<code>data/papers.json</code>。</p>";
      els.updated.textContent = "加载失败";
    }
  }

  load();
})();
